# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Demonstration mode: nothing changes while it is off; while it is on, nothing leaves the
site, the demo accounts stay as published, and the pages say what the site is."""

import os
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from hostel360 import demo
from hostel360.auth import otp
from hostel360.auth.test_otp import TEST_IP, OTPTestCase, clear_otp_keys
from hostel360.demo import accounts
from hostel360.hostel_people.testing import make_desk_user
from hostel360.setup.roles import ADMIN_ROLE, OFFICE_ROLE, WARDEN_ROLE

MISSING = object()


@contextmanager
def demo_conf(on: bool = True, **values):
	"""Site config as a demonstration site has it (``on``), or with the keys but mode off."""
	values = {demo.DEMO_MODE_KEY: 1 if on else 0, **values}
	previous = {key: frappe.local.conf.get(key, MISSING) for key in values}
	frappe.local.conf.update(values)
	try:
		yield
	finally:
		for key, value in previous.items():
			if value is MISSING:
				frappe.local.conf.pop(key, None)
			else:
				frappe.local.conf[key] = value


def messaging_settings(**values):
	"""Messaging Settings as a live site has them: channels on, test mode off."""
	settings = frappe.get_doc("Messaging Settings")
	settings.update(
		{
			"sms_enabled": 1,
			"sms_provider": "MSG91",
			"whatsapp_enabled": 1,
			"email_enabled": 1,
			"test_mode": 0,
			**values,
		}
	)
	return settings


class TestDemoModeOff(IntegrationTestCase):
	"""With the key absent (or 0) every production behaviour is unchanged."""

	def test_mode_needs_the_site_config_key(self):
		with demo_conf(on=False, hostel360_demo_otp="135790"):
			self.assertFalse(demo.is_demo_site())
			self.assertIsNone(demo.portal_boot())
			self.assertIsNone(demo.desk_boot())
		with demo_conf():
			self.assertTrue(demo.is_demo_site())

	def test_fixed_code_refused_without_demo_mode(self):
		with demo_conf(on=False, developer_mode=0, hostel360_demo_otp="135790"):
			self.assertIsNone(otp.fixed_code())
			self.assertNotEqual(otp.generate_code(6), "135790")

	def test_providers_and_gateways_work_as_before(self):
		from hostel360.hostel_communication.providers import base, sms

		with demo_conf(on=False):
			self.assertIsNone(base.demo_mode_result("MSG91"))
			result = sms.send_sms(
				"9876543210", "Hello", dlt_template_id="1", settings=messaging_settings(test_mode=1)
			)
			self.assertTrue(result.skipped)
			self.assertNotEqual(result.error, demo.DEMO_REASON)

			from hostel360.hostel_fees.gateways import refuse_on_demo_site

			refuse_on_demo_site()

	def test_pages_and_boot_without_demo(self):
		from hostel360.setup.boot import extend_bootinfo
		from hostel360.www import demo as demo_page
		from hostel360.www import hostel, verify_certificate

		with demo_conf(on=False):
			self.assertIsNone(hostel.get_boot(None)["demo"])
			self.assertIsNone(verify_certificate.demo_banner())
			boot = frappe._dict()
			extend_bootinfo(boot)
			self.assertIsNone(boot.hostel360["demo"])
			with self.assertRaises(frappe.PageDoesNotExistError):
				demo_page.get_context(frappe._dict())

	def test_listed_accounts_unlocked_while_mode_is_off(self):
		user = make_desk_user(OFFICE_ROLE)
		with demo_conf(on=False, hostel360_demo_users=[user]):
			doc = frappe.get_doc("User", user)
			doc.append("roles", {"role": WARDEN_ROLE})
			doc.new_password = "Lantern-Mango-7781"
			doc.save(ignore_permissions=True)
			self.assertIn(WARDEN_ROLE, frappe.get_roles(user))
			accounts.protect_user_on_trash(doc)


class TestDemoSignIn(OTPTestCase):
	"""The published code, only on a demonstration site and only for numbers on record."""

	def test_published_code_for_registered_numbers(self):
		with demo_conf(hostel360_demo_otp="135790"):
			mobile = self.new_mobile(otp.KIND_STUDENT)
			otp.request_code(mobile, channel="sms", ip=TEST_IP)
			self.assertEqual(self.last_code(), "135790")
			result = otp.verify_code(mobile, "135790", ip=TEST_IP)
			self.assertTrue(result["ok"])

	def test_default_code(self):
		with demo_conf():
			self.assertEqual(otp.fixed_code(), demo.DEFAULT_OTP)

	def test_code_works_without_a_fresh_send(self):
		with demo_conf():
			mobile = self.new_mobile(otp.KIND_PARENT)
			result = otp.verify_code(mobile, demo.DEFAULT_OTP, ip=TEST_IP)
			self.assertTrue(result["ok"])
			self.assertEqual(result["kind"], otp.KIND_PARENT)
			self.assertEqual(self.sent, [])

	def test_code_never_works_for_unknown_numbers(self):
		with demo_conf():
			mobile = self.new_mobile()
			otp.request_code(mobile, channel="sms", ip=TEST_IP)
			self.assertEqual(self.sent, [])
			with self.assertRaises(frappe.AuthenticationError):
				otp.verify_code(mobile, demo.DEFAULT_OTP, ip=TEST_IP)

	def test_wrong_codes_still_count(self):
		with demo_conf():
			mobile = self.new_mobile(otp.KIND_STUDENT)
			with self.assertRaises(frappe.AuthenticationError) as caught:
				otp.verify_code(mobile, "111111", ip=TEST_IP)
			self.assertIn("4 more attempts", str(caught.exception))

	def test_rate_limits_stay_on(self):
		with demo_conf():
			mobile = self.new_mobile(otp.KIND_STUDENT)
			otp.request_code(mobile, channel="sms", ip=TEST_IP)
			with self.assertRaises(frappe.RateLimitExceededError):
				otp.request_code(mobile, channel="sms", ip=TEST_IP)
			ref = otp.mobile_ref(mobile)
			frappe.cache.set(otp._key(f"lock:{ref}"), "1", ex=60)
			with self.assertRaises(frappe.RateLimitExceededError):
				otp.verify_code(mobile, demo.DEFAULT_OTP, ip=TEST_IP)


class TestDemoMutes(IntegrationTestCase):
	"""On a demonstration site nothing reaches a provider, a gateway or a webhook."""

	def setUp(self):
		super().setUp()
		for target in ("requests.post", "requests.get"):
			patcher = patch(target, side_effect=AssertionError(f"{target} called on a demonstration site"))
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_every_provider_skips_with_the_reason(self):
		from hostel360.hostel_communication.providers import email, sms, whatsapp

		settings = messaging_settings()
		with demo_conf():
			results = [
				sms.send_sms("9876543210", "Hello", dlt_template_id="1", settings=settings),
				sms.send_alert_sms("9876543210", "Alert", "Body", settings=settings),
				whatsapp.send_template("9876543210", "fee_due", "en", ["x"], settings=settings),
				whatsapp.send_authentication_code("9876543210", "otp", "en", "123456", settings=settings),
				email.send_email("someone@example.com", "Subject", "Body", settings=settings),
				email.send_email_now("someone@example.com", "Subject", "Body", settings=settings),
			]
		for result in results:
			self.assertTrue(result.skipped)
			self.assertEqual(result.error, demo.DEMO_REASON)

	def test_message_log_records_skipped(self):
		from hostel360.hostel_communication import dispatch

		with (
			demo_conf(),
			patch("hostel360.hostel_communication.dispatch.get_settings", return_value=messaging_settings()),
		):
			dispatch.send_direct("Email", "someone@example.com", "Subject", "Body")
		log = frappe.get_last_doc("Message Log", filters={"event": "direct_message"})
		self.assertEqual(log.status, "Skipped")
		self.assertEqual(log.error, demo.DEMO_REASON)

	def test_sign_in_codes_are_not_sent(self):
		from hostel360.hostel_communication import dispatch

		with (
			demo_conf(),
			patch("hostel360.hostel_communication.dispatch.get_settings", return_value=messaging_settings()),
		):
			self.assertTrue(dispatch.send_otp_message("9876543210", "246810", "SMS"))
		log = frappe.get_last_doc("Message Log", filters={"event": "otp_login"})
		self.assertEqual(log.status, "Skipped")
		self.assertEqual(log.error, demo.DEMO_REASON)

	def test_operations_alerts_never_leave(self):
		from hostel360.hostel_operations import channels, health, notification

		message = {"severity": "Critical", "event": "raised", "title": "Disk", "alert": None}
		with demo_conf():
			self.assertEqual(
				channels.post_json("https://hooks.example.com/x", {}), (False, "demonstration site")
			)
			self.assertFalse(channels.send_email(["ops@example.com"], message)[0])
			self.assertFalse(channels.send_text("SMS", "9876543210", message)[0])
			targets = [
				{"channel_type": "Generic Webhook", "label": "Hook", "url": "https://hooks.example.com/x"}
			]
			results = notification.send_to_targets(targets, message, frappe._dict())
			self.assertEqual([r["ok"] for r in results], [False])
			self.assertEqual(results[0]["info"], demo.DEMO_REASON)
			settings = frappe._dict(heartbeat_url="https://hc-ping.example.com/abc", environment="Production")
			self.assertIsNone(health.send_heartbeat(settings, frappe.utils.now_datetime()))

	def test_payment_gateways_are_never_called(self):
		from hostel360.hostel_fees import gateways, webhooks
		from hostel360.hostel_fees.gateways.base import GatewayNotConfiguredError

		settings = frappe._dict(enabled=1, gateway="Razorpay", key_id="rzp_live_x", test_mode=0)
		with demo_conf():
			with self.assertRaises(GatewayNotConfiguredError) as caught:
				gateways.get_gateway(settings)
			self.assertIn("demonstration site", str(caught.exception))
			with self.assertRaises(GatewayNotConfiguredError):
				gateways.get_settlement_gateway("Razorpay", settings)
			self.assertFalse(gateways.is_gateway_configured(settings))
			self.assertIsNone(gateways.SettlementGateways(settings).get("Razorpay"))
			self.assertEqual(webhooks.handle_razorpay_webhook(b"{}", "sig")[0], 400)
			self.assertEqual(webhooks.handle_payu_webhook({"txnid": "x"})[0], 400)

	def test_portal_settings_say_demo(self):
		from hostel360.api import portal

		with demo_conf():
			self.assertTrue(portal._session_settings()["demo"])
		with demo_conf(on=False):
			self.assertFalse(portal._session_settings()["demo"])


class TestDemoAccounts(IntegrationTestCase):
	"""The published accounts keep their password, e-mail, roles and staff record."""

	def setUp(self):
		super().setUp()
		self.user = make_desk_user(OFFICE_ROLE)
		self.other = make_desk_user(OFFICE_ROLE)

	def demo(self):
		return demo_conf(hostel360_demo_users=[self.user])

	def test_password_email_roles_and_enabled_are_locked(self):
		with self.demo():
			for change in (
				lambda doc: setattr(doc, "new_password", "Lantern-Mango-7781"),
				lambda doc: doc.append("roles", {"role": WARDEN_ROLE}),
				lambda doc: setattr(doc, "enabled", 0),
				lambda doc: setattr(doc, "simultaneous_sessions", 1),
				lambda doc: setattr(doc, "api_key", "abc123"),
			):
				doc = frappe.get_doc("User", self.user)
				change(doc)
				with self.assertRaises(frappe.PermissionError):
					doc.save(ignore_permissions=True)

	def test_email_follows_the_login_id(self):
		# Frappe keeps a user's e-mail equal to the login ID; only a rename could change it.
		with self.demo():
			doc = frappe.get_doc("User", self.user)
			doc.email = f"x-{frappe.generate_hash(length=6)}@example.com"
			doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("User", self.user, "email"), self.user)

	def test_other_changes_and_other_accounts_are_allowed(self):
		with self.demo():
			doc = frappe.get_doc("User", self.user)
			doc.language = "ta"
			doc.save(ignore_permissions=True)
			other = frappe.get_doc("User", self.other)
			other.append("roles", {"role": WARDEN_ROLE})
			other.save(ignore_permissions=True)
			self.assertIn(WARDEN_ROLE, frappe.get_roles(self.other))

	def test_delete_rename_keys_and_password_change_refused(self):
		with self.demo():
			doc = frappe.get_doc("User", self.user)
			with self.assertRaises(frappe.PermissionError):
				accounts.protect_user_on_trash(doc)
			with self.assertRaises(frappe.PermissionError):
				accounts.protect_user_rename(doc, old=self.user, new="new@example.com")
			with self.assertRaises(frappe.PermissionError):
				accounts.generate_keys(self.user)
			frappe.set_user(self.user)
			try:
				with self.assertRaises(frappe.PermissionError):
					accounts.update_password("Lantern-Mango-7781", old_password="whatever")
			finally:
				frappe.set_user("Administrator")

	def test_demo_command_may_change_them(self):
		with self.demo(), patch.dict(frappe.flags, {accounts.SETUP_FLAG: True}):
			doc = frappe.get_doc("User", self.user)
			doc.append("roles", {"role": WARDEN_ROLE})
			doc.save(ignore_permissions=True)
		self.assertIn(WARDEN_ROLE, frappe.get_roles(self.user))

	def test_staff_record_protected(self):
		from hostel360.hostel_people.testing import make_staff

		staff = make_staff("Warden", email=f"demo-warden-{frappe.generate_hash(length=6)}@example.com")
		user = frappe.db.get_value("Hostel Staff", staff.name, "user")
		with demo_conf(hostel360_demo_users=[user]):
			doc = frappe.get_doc("Hostel Staff", staff.name)
			doc.designation = "Office Staff"
			with self.assertRaises(frappe.PermissionError):
				doc.save(ignore_permissions=True)
			doc = frappe.get_doc("Hostel Staff", staff.name)
			doc.mobile_number = "9876501234"
			doc.save(ignore_permissions=True)
			with self.assertRaises(frappe.PermissionError):
				frappe.delete_doc("Hostel Staff", staff.name, ignore_permissions=True)


class TestDemoPages(IntegrationTestCase):
	"""/demo, the portal's boot data, the desk boot and the certificate page."""

	ACCOUNTS = [
		{"user": "warden@demo.invalid", "kind": "staff", "role": WARDEN_ROLE, "name": "Dr. K. Senthilkumar"},
		{"user": "admin@demo.invalid", "kind": "staff", "role": ADMIN_ROLE, "name": "Revathi Sundaram"},
		{
			"user": "s1@demo.invalid",
			"kind": "student",
			"role": "Student, Boys Hostel I - fees due",
			"name": "Ragul Velu",
			"mobile": "9000000001",
			"hostel": "Boys Hostel I",
			"note": "dues",
		},
		{
			"user": "p1@demo.invalid",
			"kind": "parent",
			"role": "Parent of Ragul and Harini",
			"name": "Velu",
			"mobile": "9000000012",
			"note": "two_wards",
			"wards": ["Ragul", "Harini"],
		},
	]

	def demo(self):
		return demo_conf(
			hostel360_demo_password="Lotus-Kolam-4821",
			hostel360_demo_otp="246810",
			hostel360_demo_accounts=self.ACCOUNTS,
		)

	def test_demo_page_lists_the_sign_ins(self):
		from hostel360.www import demo as demo_page

		with self.demo():
			context = demo_page.get_context(frappe._dict())
			path = os.path.join(frappe.get_app_path("hostel360"), "www", "demo.html")
			with open(path, encoding="utf-8") as f:
				html = frappe.render_template(f.read(), context)
		self.assertIn("Lotus-Kolam-4821", html)
		self.assertIn("246810", html)
		self.assertIn("warden@demo.invalid", html)
		self.assertIn("9000000001", html)
		self.assertIn("Demonstration site", html)

	def test_portal_boot_and_desk_boot(self):
		from hostel360.setup.boot import extend_bootinfo
		from hostel360.www import hostel

		with self.demo():
			boot = hostel.get_boot(None)["demo"]
			desk = frappe._dict()
			extend_bootinfo(desk)
		self.assertEqual(boot["otp"], "246810")
		self.assertEqual([a["mobile"] for a in boot["accounts"]], ["9000000001", "9000000012"])
		self.assertEqual(boot["accounts"][1]["wards"], ["Ragul", "Harini"])
		# Staff accounts and the password are only on /demo, never in the portal page.
		self.assertNotIn("Lotus-Kolam-4821", frappe.as_json(boot))
		self.assertEqual(desk.hostel360["demo"], {"info_url": "/demo"})

	def test_certificate_page_banner(self):
		from hostel360.www import verify_certificate

		with self.demo():
			banner = verify_certificate.demo_banner()
		self.assertEqual(banner["url"], "/demo")
		self.assertIn("fictitious", banner["en"])


class TestRedate(IntegrationTestCase):
	def test_interval(self):
		from hostel360.demo.redate import interval

		self.assertEqual(interval("2026-10-01 22:00:00", "2026-10-03 06:00:00"), (32 * 3600, 2))

	def test_calendar_fields_stay(self):
		from hostel360.demo.redate import doctypes_to_shift, shift_assignments

		menu = " ".join(shift_assignments("Mess Menu", 60, 1))
		self.assertNotIn("week_start_date", menu)
		self.assertIn("published_on", menu)
		self.assertNotIn("from_date", " ".join(shift_assignments("Mess Bill Run", 60, 1)))
		self.assertIn("due_date", " ".join(shift_assignments("Mess Bill Run", 60, 1)))
		self.assertNotIn("Hostel Academic Year", doctypes_to_shift())
		self.assertIn("Home Visit Leave", doctypes_to_shift())

	def test_shift_moves_dates_and_times(self):
		from hostel360.demo.redate import shift_dates
		from hostel360.hostel_operations.metrics import record_metrics

		metric = f"demo_test_{frappe.generate_hash(length=6)}"
		record_metrics([{"metric": metric, "value": 1, "recorded_at": "2026-10-01 10:00:00"}], source="Test")
		shift_dates(3600, 1, doctypes=["Ops Metric Sample"])
		recorded = frappe.db.get_value("Ops Metric Sample", {"metric": metric}, "recorded_at")
		self.assertEqual(str(recorded), "2026-10-01 11:00:00")


class TestDemoSiteSafety(IntegrationTestCase):
	def test_refuses_sites_with_real_records(self):
		from hostel360.demo.site import DemoSiteError, check_only_demo_data
		from hostel360.hostel_people.testing import make_student

		make_student()
		with self.assertRaises(DemoSiteError):
			check_only_demo_data()

	def test_reset_refused_unless_demo(self):
		from hostel360.demo.site import DemoSiteError, reset_demo_site

		with demo_conf(on=False), self.assertRaises(DemoSiteError):
			reset_demo_site()

	def test_reset_needs_a_snapshot(self):
		from hostel360.demo import snapshot
		from hostel360.demo.site import DemoSiteError, reset_demo_site

		with demo_conf(), patch.object(snapshot, "read_manifest", return_value=None):
			with self.assertRaises(DemoSiteError):
				reset_demo_site()

	def test_demo_password_passes_the_policy(self):
		from frappe.utils.password_strength import test_password_strength

		from hostel360.demo.site import new_password

		passwords = {new_password() for _i in range(10)}
		self.assertGreater(len(passwords), 1)
		for password in passwords:
			self.assertGreaterEqual(test_password_strength(password).get("score", 0), 3)


def tearDownModule():
	for mobile in ("9876543210",):
		clear_otp_keys(mobile, TEST_IP)
