# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import random
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.auth import otp

TEST_IP = "203.0.113.25"


def random_mobile() -> str:
	return random.choice("6789") + "".join(random.choice("0123456789") for _ in range(9))


def clear_otp_keys(mobile: str, ip: str | None = None) -> None:
	ref = otp.mobile_ref(mobile)
	names = [f"{prefix}:{ref}" for prefix in ("code", "attempts", "cooldown", "sends", "fail", "lock")]
	if ip:
		names += [f"ip:{otp.ip_ref(ip)}", f"ipfail:{otp.ip_ref(ip)}"]
	otp._delete(*names)


def error_text(callable_, *args, **kwargs) -> str:
	try:
		callable_(*args, **kwargs)
	except (frappe.AuthenticationError, frappe.ValidationError) as e:
		return f"{type(e).__name__}: {e}"
	return "no error"


class OTPTestCase(IntegrationTestCase):
	"""Shared set-up: identities and delivery are stubbed; Redis keys are cleaned per test."""

	def setUp(self):
		super().setUp()
		self.known: dict[str, list] = {}
		self.sent: list[tuple[str, str, str]] = []
		self.delivery_ok = {"SMS": True, "WhatsApp": True}
		self.mobiles: list[str] = []

		self.timeouts = []
		self.languages = []

		def fake_send(mobile, code, channel, timeout=None, language=None):
			self.sent.append((mobile, code, channel))
			self.timeouts.append(timeout)
			self.languages.append(language)
			return self.delivery_ok.get(channel, False)

		for target, kwargs in (
			("hostel360.hostel_communication.dispatch.send_otp_message", {"side_effect": fake_send}),
			(
				"hostel360.auth.otp.resolve_identities",
				{"side_effect": lambda mobile: self.known.get(mobile, [])},
			),
			("hostel360.auth.otp._sleep", {}),
			("hostel360.auth.otp._record_failed_sign_in", {}),
		):
			patcher = patch(target, **kwargs)
			setattr(self, "mock_" + target.rsplit(".", 1)[1].lstrip("_"), patcher.start())
			self.addCleanup(patcher.stop)
		self.addCleanup(self._clear_all)

	def _clear_all(self):
		for mobile in self.mobiles:
			clear_otp_keys(mobile, TEST_IP)

	def new_mobile(self, *kinds: str) -> str:
		mobile = random_mobile()
		self.mobiles.append(mobile)
		clear_otp_keys(mobile, TEST_IP)
		self.known[mobile] = [
			frappe._dict(
				kind=kind,
				doctype=otp.KIND_DOCTYPES[kind],
				name=f"{kind}-{frappe.generate_hash(length=6)}",
			)
			for kind in kinds
		]
		return mobile

	def last_code(self) -> str:
		return self.sent[-1][1]

	def allow_resend(self, mobile: str) -> None:
		otp._delete(f"cooldown:{otp.mobile_ref(mobile)}")


class TestOTPFlow(OTPTestCase):
	def test_happy_path(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		response = otp.request_code(mobile, channel="sms", ip=TEST_IP)

		self.assertEqual(set(response), {"sent", "masked", "resend_after", "expires_in"})
		self.assertTrue(response["sent"])
		self.assertEqual(response["masked"], f"{mobile[:2]}xxxxxx{mobile[-2:]}")
		self.assertEqual(len(self.sent), 1)
		code = self.last_code()
		self.assertEqual(len(code), 6)
		self.assertTrue(code.isdigit())

		result = otp.verify_code(mobile, code, ip=TEST_IP)
		self.assertTrue(result["ok"])
		self.assertEqual(result["kind"], otp.KIND_STUDENT)
		self.assertEqual(result["doctype"], "Hostel Student")

		# A code works once.
		with self.assertRaises(frappe.AuthenticationError):
			otp.verify_code(mobile, code, ip=TEST_IP)

	def test_code_is_stored_only_as_hmac(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		otp.request_code(mobile)
		code = self.last_code()
		raw = frappe.cache.get(otp._key(f"code:{otp.mobile_ref(mobile)}")).decode()
		self.assertNotIn(code, raw)
		self.assertNotIn(mobile, raw)
		self.assertIn(otp.code_digest(mobile, code), raw)
		self.assertGreater(frappe.cache.ttl(otp._key(f"code:{otp.mobile_ref(mobile)}")), 0)

	def test_wrong_code_counts_attempts_and_invalidates(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		otp.request_code(mobile)
		code = self.last_code()
		wrong = "000000" if code != "000000" else "111111"
		policy = otp.get_policy()

		with self.assertRaises(frappe.AuthenticationError) as ctx:
			otp.verify_code(mobile, wrong)
		self.assertIn(str(policy.max_attempts - 1), str(ctx.exception))

		for _attempt in range(policy.max_attempts - 1):
			with self.assertRaises(frappe.AuthenticationError):
				otp.verify_code(mobile, wrong)

		# The right code no longer works once the attempts are used up.
		with self.assertRaises(frappe.AuthenticationError):
			otp.verify_code(mobile, code)

	def test_expired_code_is_rejected(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		otp.request_code(mobile)
		code = self.last_code()
		later = otp._now() + otp.get_policy().expiry + 5
		with patch("hostel360.auth.otp._now", return_value=later):
			with self.assertRaises(frappe.AuthenticationError) as ctx:
				otp.verify_code(mobile, code)
		self.assertIn("expired", str(ctx.exception))

	def test_resend_cooldown(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		otp.request_code(mobile)
		with self.assertRaises(frappe.RateLimitExceededError):
			otp.request_code(mobile)
		self.assertEqual(len(self.sent), 1)

	def test_hourly_limit_per_number(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		limit = otp.get_policy().sends_per_hour
		for _send in range(limit):
			otp.request_code(mobile)
			self.allow_resend(mobile)
		with self.assertRaises(frappe.RateLimitExceededError):
			otp.request_code(mobile)
		self.assertEqual(len(self.sent), limit)

	def test_ip_limit_and_trusted_networks(self):
		policy = otp.get_policy()
		policy.sends_per_ip = 2
		first, second, third, fourth = (self.new_mobile(otp.KIND_STUDENT) for _i in range(4))
		with patch("hostel360.auth.otp.get_policy", return_value=policy):
			otp.request_code(first, ip=TEST_IP)
			otp.request_code(second, ip=TEST_IP)
			with self.assertRaises(frappe.RateLimitExceededError):
				otp.request_code(third, ip=TEST_IP)

			# The campus gateway can be exempted; per-number limits still apply there.
			policy.trusted_networks = otp.parse_networks("203.0.113.0/24")
			otp.request_code(fourth, ip=TEST_IP)
		self.assertEqual([s[0] for s in self.sent], [first, second, fourth])

	def test_unknown_number_gets_identical_response(self):
		known = self.new_mobile(otp.KIND_STUDENT)
		unknown = self.new_mobile()
		known_response = otp.request_code(known)
		self.mock_sleep.reset_mock()
		unknown_response = otp.request_code(unknown)

		self.assertEqual(set(known_response), set(unknown_response))
		for key in ("sent", "resend_after", "expires_in"):
			self.assertEqual(known_response[key], unknown_response[key])
		self.assertEqual(unknown_response["masked"], f"{unknown[:2]}xxxxxx{unknown[-2:]}")
		# Nothing was sent to the unknown number, but the reply was delayed like a real send.
		self.assertEqual([s[0] for s in self.sent], [known])
		self.assertTrue(self.mock_sleep.called)

		# Unknown numbers are throttled exactly like known ones.
		with self.assertRaises(frappe.RateLimitExceededError):
			otp.request_code(unknown)

	def test_unknown_number_verification_looks_like_an_expired_code(self):
		known = self.new_mobile(otp.KIND_STUDENT)
		unknown = self.new_mobile()
		with self.assertRaises(frappe.AuthenticationError) as known_error:
			otp.verify_code(known, "123456")
		with self.assertRaises(frappe.AuthenticationError) as unknown_error:
			otp.verify_code(unknown, "123456")
		self.assertEqual(str(known_error.exception), str(unknown_error.exception))

	def test_unknown_number_verification_matches_a_registered_one_after_a_request(self):
		# send_otp then verify_otp must not tell a resident's number from any other.
		known = self.new_mobile(otp.KIND_STUDENT)
		unknown = self.new_mobile()
		otp.request_code(known, ip=TEST_IP)
		wrong = "000000" if self.last_code() != "000000" else "111111"
		otp.request_code(unknown, ip=TEST_IP)
		self.assertEqual(len(self.sent), 1)
		# A code is stored for the unknown number too; it is not digits, so it never matches.
		self.assertIsNotNone(otp._load_code(otp.mobile_ref(unknown)))
		for _attempt in range(otp.get_policy().max_attempts + 1):
			self.assertEqual(
				error_text(otp.verify_code, known, wrong, ip=TEST_IP),
				error_text(otp.verify_code, unknown, wrong, ip=TEST_IP),
			)

	def test_shared_number_asks_who_is_signing_in(self):
		mobile = self.new_mobile(otp.KIND_STUDENT, otp.KIND_PARENT)
		otp.request_code(mobile)
		code = self.last_code()

		result = otp.verify_code(mobile, code)
		self.assertEqual(result, {"ok": False, "choose": ["student", "parent"]})

		# The code was not consumed by the question.
		result = otp.verify_code(mobile, code, kind="parent")
		self.assertTrue(result["ok"])
		self.assertEqual(result["kind"], otp.KIND_PARENT)
		self.assertEqual(result["doctype"], "Hostel Guardian")

	def test_kind_that_does_not_match_is_refused(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		otp.request_code(mobile)
		with self.assertRaises(frappe.AuthenticationError):
			otp.verify_code(mobile, self.last_code(), kind="parent")

	def test_number_is_locked_after_repeated_failures(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		policy = otp.get_policy()
		failures = 0
		while failures < otp.FAILED_VERIFICATIONS_PER_HOUR:
			self.allow_resend(mobile)
			otp.request_code(mobile)
			wrong = "000000" if self.last_code() != "000000" else "111111"
			for _attempt in range(min(policy.max_attempts, otp.FAILED_VERIFICATIONS_PER_HOUR - failures)):
				with self.assertRaises((frappe.AuthenticationError, frappe.RateLimitExceededError)):
					otp.verify_code(mobile, wrong)
				failures += 1
		with self.assertRaises(frappe.RateLimitExceededError):
			otp.verify_code(mobile, "999999")
		self.allow_resend(mobile)
		with self.assertRaises(frappe.RateLimitExceededError):
			otp.request_code(mobile)

	def test_verification_without_a_code_never_locks_the_number(self):
		# No code was sent, so nothing can be guessed: such attempts count for the network
		# only. Otherwise anyone could keep a resident out without a single SMS going out.
		mobile = self.new_mobile(otp.KIND_STUDENT)
		for _attempt in range(otp.FAILED_VERIFICATIONS_PER_HOUR * 2):
			with self.assertRaises(frappe.AuthenticationError):
				otp.verify_code(mobile, "999999", ip=TEST_IP)
		self.assertEqual(otp._ttl(f"lock:{otp.mobile_ref(mobile)}"), 0)
		self.assertEqual(otp._count(f"ipfail:{otp.ip_ref(TEST_IP)}"), otp.FAILED_VERIFICATIONS_PER_HOUR * 2)
		otp.request_code(mobile, ip=TEST_IP)
		self.assertTrue(otp.verify_code(mobile, self.last_code(), ip=TEST_IP)["ok"])

	def test_guesses_are_reserved_before_they_are_compared(self):
		# Simultaneous requests each take their attempt before comparing, so the ones past
		# the limit are refused without a comparison - even when they carry the right code.
		mobile = self.new_mobile(otp.KIND_STUDENT)
		policy = otp.get_policy()
		ref = otp.mobile_ref(mobile)
		otp.request_code(mobile)
		code = self.last_code()
		for _other_request in range(policy.max_attempts):
			otp._incr_window(f"attempts:{ref}", policy.expiry)
		with patch("hostel360.auth.otp.code_digest", wraps=otp.code_digest) as digest:
			with self.assertRaises(frappe.AuthenticationError) as refused:
				otp.verify_code(mobile, code)
		digest.assert_not_called()
		self.assertIn("too many times", str(refused.exception))

		# The same for the hourly failure limit of the number.
		mobile = self.new_mobile(otp.KIND_STUDENT)
		ref = otp.mobile_ref(mobile)
		otp.request_code(mobile)
		code = self.last_code()
		for _other_request in range(otp.FAILED_VERIFICATIONS_PER_HOUR):
			otp._incr_window(f"fail:{ref}", otp.HOUR)
		with patch("hostel360.auth.otp.code_digest", wraps=otp.code_digest) as digest:
			with self.assertRaises(frappe.RateLimitExceededError):
				otp.verify_code(mobile, code)
		digest.assert_not_called()
		self.assertGreater(otp._ttl(f"lock:{ref}"), 0)

	def test_right_code_does_not_count_as_a_failure(self):
		mobile = self.new_mobile(otp.KIND_STUDENT, otp.KIND_PARENT)
		ref = otp.mobile_ref(mobile)
		otp.request_code(mobile, ip=TEST_IP)
		code = self.last_code()
		policy = otp.get_policy()
		# Asking who is signing in neither uses an attempt nor counts as a failure.
		for _question in range(policy.max_attempts + 1):
			self.assertFalse(otp.verify_code(mobile, code, ip=TEST_IP)["ok"])
		self.assertEqual(otp._count(f"attempts:{ref}"), 0)
		self.assertEqual(otp._count(f"fail:{ref}"), 0)
		self.assertEqual(otp._count(f"ipfail:{otp.ip_ref(TEST_IP)}"), 0)
		self.assertTrue(otp.verify_code(mobile, code, kind="student", ip=TEST_IP)["ok"])

	def test_invalid_mobile_number(self):
		for value in ("12345", "5123456789", "abcdefghij", ""):
			with self.assertRaises(frappe.ValidationError):
				otp.request_code(value)

	def test_non_numeric_code_is_refused_without_counting(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		otp.request_code(mobile)
		with self.assertRaises(frappe.ValidationError):
			otp.verify_code(mobile, "12ab56")
		self.assertTrue(otp.verify_code(mobile, self.last_code())["ok"])

	def test_delivery_falls_back_to_the_other_channel(self):
		policy = otp.get_policy()
		policy.channels = ["SMS", "WhatsApp"]
		self.delivery_ok["WhatsApp"] = False
		mobile = self.new_mobile(otp.KIND_PARENT)
		with patch("hostel360.auth.otp.get_policy", return_value=policy):
			otp.request_code(mobile, channel="whatsapp")
		self.assertEqual([s[2] for s in self.sent], ["WhatsApp", "SMS"])

	def test_requested_channel_must_be_allowed(self):
		policy = otp.get_policy()
		policy.channels = ["SMS"]
		mobile = self.new_mobile(otp.KIND_STUDENT)
		with patch("hostel360.auth.otp.get_policy", return_value=policy):
			otp.request_code(mobile, channel="whatsapp")
		self.assertEqual([s[2] for s in self.sent], ["SMS"])


class TestBoundedDelivery(OTPTestCase):
	"""The code goes out inside the sign-in request, so the send must stay short."""

	def test_provider_calls_get_a_short_timeout(self):
		mobile = self.new_mobile(otp.KIND_STUDENT)
		otp.request_code(mobile, channel="sms")
		connect, read = self.timeouts[-1]
		self.assertLessEqual(connect, otp.OTP_CONNECT_TIMEOUT)
		self.assertLessEqual(read, otp.OTP_READ_TIMEOUT)

	def test_time_budget_stops_further_channels(self):
		policy = otp.get_policy()
		policy.channels = ["SMS", "WhatsApp"]
		self.delivery_ok = {"SMS": False, "WhatsApp": False}
		# request start, first channel, second channel, send-time bookkeeping
		clock = iter([0.0, 0.0, otp.OTP_SEND_BUDGET_SECONDS, otp.OTP_SEND_BUDGET_SECONDS])
		mobile = self.new_mobile(otp.KIND_STUDENT)
		with (
			patch("hostel360.auth.otp.get_policy", return_value=policy),
			patch("hostel360.auth.otp._clock", side_effect=lambda: next(clock)),
		):
			otp.request_code(mobile, channel="sms")
		self.assertEqual([s[2] for s in self.sent], ["SMS"])

	def test_sends_in_flight_are_capped(self):
		held = [otp._acquire_send_slot() for _slot in range(otp.MAX_SENDS_IN_FLIGHT)]
		self.addCleanup(lambda: [otp._release_send_slot(token) for token in held])
		self.assertTrue(all(held))
		mobile = self.new_mobile(otp.KIND_STUDENT)
		response = otp.request_code(mobile)
		self.assertTrue(response["sent"])  # same answer as always
		self.assertEqual(self.sent, [])
		otp._release_send_slot(held.pop())
		self.allow_resend(mobile)
		otp.request_code(mobile)
		self.assertEqual(len(self.sent), 1)


class TestCodeLanguage(OTPTestCase):
	def test_code_follows_the_language_on_the_device(self):
		mobile = self.new_mobile(otp.KIND_PARENT)
		with patch.object(frappe.local, "lang", "ta"):
			otp.request_code(mobile)
		self.allow_resend(mobile)
		with patch.object(frappe.local, "lang", "en"):
			otp.request_code(mobile)
		self.allow_resend(mobile)
		otp.request_code(mobile, language="ta-IN")
		self.assertEqual(self.languages, ["ta", "en", "ta"])

	def test_unknown_language_means_english(self):
		with patch.object(frappe.local, "lang", "hi"):
			self.assertEqual(otp.request_language(), "en")


class TestFixedDevelopmentCode(OTPTestCase):
	def test_fixed_code_is_ignored_without_developer_mode(self):
		with patch.dict(frappe.conf, {"developer_mode": 0, "hostel360_fixed_otp": "123456"}):
			self.assertIsNone(otp.fixed_code())
			codes = {otp.generate_code(6) for _i in range(5)}
			self.assertTrue(all(len(c) == 6 and c.isdigit() for c in codes))
			self.assertGreater(len(codes), 1)

	def test_fixed_code_in_developer_mode(self):
		with patch.dict(frappe.conf, {"developer_mode": 1, "hostel360_fixed_otp": "246810"}):
			self.assertEqual(otp.fixed_code(), "246810")
			mobile = self.new_mobile(otp.KIND_STUDENT)
			otp.request_code(mobile)
			self.assertEqual(self.last_code(), "246810")
			self.assertTrue(otp.verify_code(mobile, "246810")["ok"])

	def test_malformed_fixed_code_is_ignored(self):
		with patch.dict(frappe.conf, {"developer_mode": 1, "hostel360_fixed_otp": "12ab"}):
			self.assertIsNone(otp.fixed_code())


class TestAuthAPI(OTPTestCase):
	def test_send_otp_endpoint(self):
		from hostel360.api.auth import send_otp

		mobile = self.new_mobile(otp.KIND_STUDENT)
		response = send_otp(mobile=f"+91 {mobile[:5]} {mobile[5:]}", channel="sms")
		self.assertTrue(response["sent"])
		self.assertEqual(self.sent[-1][0], mobile)

	def test_verify_otp_starts_a_portal_session(self):
		from hostel360.api.auth import verify_otp

		mobile = self.new_mobile(otp.KIND_STUDENT)
		identity = self.known[mobile][0]
		otp.request_code(mobile)
		login_manager = MagicMock()
		with (
			patch(
				"hostel360.hostel_people.services.ensure_portal_user", return_value="student@example.com"
			) as ensure,
			patch.object(frappe.local, "login_manager", login_manager, create=True),
		):
			response = verify_otp(mobile=mobile, otp=self.last_code())

		self.assertEqual(response, {"ok": True, "kind": "student", "redirect": "/hostel"})
		ensure.assert_called_once_with("Hostel Student", identity.name)
		login_manager.login_as.assert_called_once_with("student@example.com")

	def test_verify_otp_returns_choice_for_shared_number(self):
		from hostel360.api.auth import verify_otp

		mobile = self.new_mobile(otp.KIND_STUDENT, otp.KIND_PARENT)
		otp.request_code(mobile)
		login_manager = MagicMock()
		with patch.object(frappe.local, "login_manager", login_manager, create=True):
			response = verify_otp(mobile=mobile, otp=self.last_code())
		self.assertEqual(response, {"ok": False, "choose": ["student", "parent"]})
		login_manager.login_as.assert_not_called()

	def test_staff_can_lift_a_sign_in_lock(self):
		from hostel360.api.auth import unlock_sign_in
		from hostel360.setup.roles import OFFICE_ROLE, STUDENT_ROLE

		mobile = self.new_mobile(otp.KIND_STUDENT)
		ref = otp.mobile_ref(mobile)
		frappe.cache.set(otp._key(f"lock:{ref}"), "1", ex=otp.LOCKOUT_SECONDS)

		def user_with(role):
			doc = frappe.get_doc(
				{
					"doctype": "User",
					"email": f"otp-{frappe.generate_hash(length=8)}@example.com",
					"first_name": "Otp",
					"send_welcome_email": 0,
					"roles": [{"role": role}],
				}
			)
			doc.flags.no_welcome_mail = True
			return doc.insert(ignore_permissions=True).name

		with self.set_user(user_with(STUDENT_ROLE)), self.assertRaises(frappe.PermissionError):
			unlock_sign_in(mobile)
		self.assertGreater(otp._ttl(f"lock:{ref}"), 0)

		with self.set_user(user_with(OFFICE_ROLE)):
			self.assertEqual(unlock_sign_in(mobile), {"ok": True})
		self.assertEqual(otp._ttl(f"lock:{ref}"), 0)
		otp.request_code(mobile)
		self.assertEqual(len(self.sent), 1)


class TestFloodLimit(IntegrationTestCase):
	def test_trusted_networks_get_a_far_higher_ceiling(self):
		from hostel360.api import auth

		policy = otp.get_policy()
		policy.trusted_networks = otp.parse_networks("203.0.113.0/24")
		with patch("hostel360.auth.otp.get_policy", return_value=policy):
			with patch.object(frappe.local, "request_ip", TEST_IP, create=True):
				self.assertEqual(auth.flood_limit(), auth.TRUSTED_FLOOD_LIMIT_PER_HOUR)
			with patch.object(frappe.local, "request_ip", "198.51.100.7", create=True):
				self.assertEqual(auth.flood_limit(), auth.FLOOD_LIMIT_PER_HOUR)
			with patch.object(frappe.local, "request_ip", None, create=True):
				self.assertEqual(auth.flood_limit(), auth.FLOOD_LIMIT_PER_HOUR)
		self.assertGreater(auth.TRUSTED_FLOOD_LIMIT_PER_HOUR, auth.FLOOD_LIMIT_PER_HOUR * 10)

	def test_unreadable_settings_keep_the_ordinary_ceiling(self):
		from hostel360.api import auth

		with patch("hostel360.auth.otp.get_policy", side_effect=RuntimeError("no settings")):
			self.assertEqual(auth.flood_limit(), auth.FLOOD_LIMIT_PER_HOUR)

	def test_endpoints_use_the_callable_limit(self):
		from hostel360.api import auth

		def bound_values(function, depth=0):
			# The rate_limit wrapper keeps its ``limit`` argument in its closure, under
			# Frappe's own whitelist wrapper.
			for cell in getattr(function, "__closure__", None) or ():
				value = cell.cell_contents
				yield value
				if callable(value) and depth < 4:
					yield from bound_values(value, depth + 1)

		for endpoint in (auth.send_otp, auth.verify_otp):
			self.assertIn(auth.flood_limit, list(bound_values(endpoint)), endpoint.__name__)


class TestIdentityResolution(IntegrationTestCase):
	"""Real Hostel Student and Hostel Guardian records."""

	def setUp(self):
		super().setUp()
		if not frappe.db.exists("DocType", "Hostel Student"):
			self.skipTest("Hostel People is not installed on this site")
		self._portal_flags = {
			field: int(otp.portal_enabled(field))
			for field in ("student_portal_enabled", "parent_portal_enabled")
		}
		self.addCleanup(self._restore_portal_flags)

	def _restore_portal_flags(self):
		for field, value in self._portal_flags.items():
			frappe.db.set_single_value("Hostel Settings", field, value)

	def make_student(self, **state) -> frappe._dict:
		doc = frappe.get_doc(
			{
				"doctype": "Hostel Student",
				"register_number": f"OTP{frappe.generate_hash(length=8).upper()}",
				"first_name": "Otp",
				"last_name": "Tester",
				"gender": "Male",
				"mobile_number": random_mobile(),
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		# Status and validation normally change through Hostel People's own actions.
		frappe.db.set_value("Hostel Student", doc.name, {"validation_status": "Validated", **state})
		doc.reload()
		return doc

	def make_guardian(self, student=None, **state):
		guardian = frappe.get_doc(
			{
				"doctype": "Hostel Guardian",
				"guardian_name": "Otp Parent",
				"relation": "Father",
				"mobile_number": random_mobile(),
			}
		).insert(ignore_permissions=True)
		if student:
			student.append("guardians", {"guardian": guardian.name, "relation": "Father"})
			student.save(ignore_permissions=True)
		if state:
			frappe.db.set_value("Hostel Guardian", guardian.name, state)
			guardian.reload()
		return guardian

	def test_active_student_and_linked_guardian(self):
		student = self.make_student()
		guardian = self.make_guardian(student)
		self.assertEqual(
			[(i.kind, i.name) for i in otp.resolve_identities(student.mobile_number)],
			[("student", student.name)],
		)
		self.assertEqual(
			[(i.kind, i.name) for i in otp.resolve_identities(guardian.mobile_number)],
			[("parent", guardian.name)],
		)

	def test_unvalidated_students_and_their_parents_cannot_sign_in(self):
		# Tender 3.1 and 4.1: validated against the college register before the first sign-in.
		pending = self.make_student(validation_status="Pending")
		self.assertEqual(otp.resolve_identities(pending.mobile_number), [])
		self.assertEqual(otp.resolve_identities(self.make_guardian(pending).mobile_number), [])

		left = self.make_student(status="Vacated")
		self.assertEqual(otp.resolve_identities(self.make_guardian(left).mobile_number), [])

		# One validated resident ward is enough for the parent.
		guardian = self.make_guardian(pending)
		resident = self.make_student()
		resident.append("guardians", {"guardian": guardian.name, "relation": "Father"})
		resident.save(ignore_permissions=True)
		self.assertEqual(
			[(i.kind, i.name) for i in otp.resolve_identities(guardian.mobile_number)],
			[("parent", guardian.name)],
		)

	def test_closed_or_rejected_records_cannot_sign_in(self):
		for values in ({"status": "Vacated"}, {"status": "Alumni"}, {"validation_status": "Rejected"}):
			student = self.make_student(**values)
			self.assertEqual(otp.resolve_identities(student.mobile_number), [], values)

		rejected = self.make_guardian(self.make_student(), validation_status="Rejected")
		self.assertEqual(otp.resolve_identities(rejected.mobile_number), [])

		unlinked = self.make_guardian()
		self.assertEqual(otp.resolve_identities(unlinked.mobile_number), [])

	def test_portal_switches_are_respected(self):
		student = self.make_student()
		guardian = self.make_guardian(student)
		frappe.db.set_single_value("Hostel Settings", "student_portal_enabled", 0)
		self.assertEqual(otp.resolve_identities(student.mobile_number), [])
		self.assertEqual(len(otp.resolve_identities(guardian.mobile_number)), 1)

		frappe.db.set_single_value("Hostel Settings", "parent_portal_enabled", 0)
		self.assertEqual(otp.resolve_identities(guardian.mobile_number), [])
