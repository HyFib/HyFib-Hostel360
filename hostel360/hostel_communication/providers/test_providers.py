# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import json
from unittest.mock import patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from hostel360.hostel_communication.providers import base, email, sms, whatsapp
from hostel360.hostel_communication.providers.base import DeliveryResult

MOBILE = "9876543210"


class FakeResponse:
	def __init__(self, status_code=200, payload=None, text=None):
		self.status_code = status_code
		self.ok = 200 <= status_code < 300
		self._payload = payload
		self.text = text if text is not None else json.dumps(payload or {})

	def json(self):
		if self._payload is None:
			raise ValueError("not json")
		return self._payload


def settings(**values):
	doc = frappe.get_doc("Messaging Settings")
	doc.update(
		{
			"sms_enabled": 1,
			"whatsapp_enabled": 1,
			"email_enabled": 1,
			"test_mode": 0,
			"sms_provider": "MSG91",
			"sms_api_url": "",
			"sms_sender_id": "ACGCET",
			"dlt_entity_id": "1201160000000000001",
			"whatsapp_phone_number_id": "106540352242922",
			"whatsapp_api_version": "v21.0",
			"provider_timeout_seconds": 7,
			**values,
		}
	)
	return doc


class TestMSG91(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		for target in ("sms.get_secret", "whatsapp.get_secret"):
			patcher = patch(f"hostel360.hostel_communication.providers.{target}", return_value="secret-key")
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_flow_payload_and_success(self):
		response = FakeResponse(200, {"type": "success", "message": "3567686b6e44303636363136"})
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.post", return_value=response
		) as post:
			result = sms.send_sms(
				MOBILE,
				"Hostel fee of Rs.45,250 ...",
				msg91_template_id="6650a1b2c3d4e5f6a7b8c9d0",
				variables={"student_name": "Karthikeyan Ramasamy Subramanian", "amount": 45250, "flag": True},
				parameters=["Rs.45,250", "Karthikeyan R"],
				settings=settings(),
			)
		self.assertTrue(result.ok)
		self.assertEqual(result.message_id, "3567686b6e44303636363136")

		url = post.call_args.args[0]
		kwargs = post.call_args.kwargs
		self.assertEqual(url, sms.MSG91_FLOW_URL)
		self.assertEqual(kwargs["headers"]["authkey"], "secret-key")
		self.assertEqual(kwargs["timeout"], (base.CONNECT_TIMEOUT, 7))
		payload = kwargs["json"]
		self.assertEqual(payload["template_id"], "6650a1b2c3d4e5f6a7b8c9d0")
		self.assertEqual(payload["sender"], "ACGCET")
		recipient = payload["recipients"][0]
		self.assertEqual(recipient["mobiles"], "919876543210")
		self.assertEqual(recipient["VAR1"], "Rs.45,250")
		self.assertEqual(recipient["VAR2"], "Karthikeyan R")
		self.assertEqual(recipient["amount"], "45250")
		# DLT limits each variable to 30 characters; booleans are not template values.
		self.assertLessEqual(len(recipient["student_name"]), 30)
		self.assertNotIn("flag", recipient)

	def test_text_with_dlt_template_id_when_no_flow_is_set(self):
		response = FakeResponse(200, {"type": "success", "message": "3567686b6e44303636363137"})
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.post", return_value=response
		) as post:
			result = sms.send_sms(
				MOBILE,
				"Payment of Rs.4,000 received. -ACGCET Hostel",
				dlt_template_id="1207170000000012345",
				settings=settings(),
			)
		self.assertTrue(result.ok)
		self.assertEqual(post.call_args.args[0], sms.MSG91_SENDHTTP_URL)
		data = post.call_args.kwargs["data"]
		self.assertEqual(data["DLT_TE_ID"], "1207170000000012345")
		self.assertEqual(data["message"], "Payment of Rs.4,000 received. -ACGCET Hostel")
		self.assertEqual(data["mobiles"], "919876543210")
		self.assertEqual(data["sender"], "ACGCET")
		self.assertEqual(data["route"], sms.MSG91_TRANSACTIONAL_ROUTE)
		self.assertEqual(data["authkey"], "secret-key")

	def test_tamil_text_is_sent_as_unicode(self):
		response = FakeResponse(200, {"type": "success", "message": "3567686b6e44303636363138"})
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.post", return_value=response
		) as post:
			sms.send_sms(
				MOBILE,
				"ரசீது எண் RCPT-1. நன்றி. -ACGCET விடுதி",
				dlt_template_id="1207170000000012346",
				settings=settings(),
			)
			self.assertEqual(post.call_args.kwargs["data"]["unicode"], "1")
			sms.send_sms(MOBILE, "Receipt RCPT-1", dlt_template_id="1207170000000012345", settings=settings())
			self.assertNotIn("unicode", post.call_args.kwargs["data"])

	def test_errors_reported_with_http_200(self):
		response = FakeResponse(200, {"type": "error", "message": "Insufficient balance"})
		with patch("hostel360.hostel_communication.providers.sms.requests.post", return_value=response):
			result = sms.send_sms(MOBILE, "x", msg91_template_id="t1", settings=settings())
		self.assertFalse(result.ok)
		self.assertTrue(result.retryable)
		self.assertIn("Insufficient balance", result.error)

	def test_auth_failure_is_not_retried(self):
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.post",
			return_value=FakeResponse(401, {"type": "error", "message": "Authentication failure"}),
		):
			result = sms.send_sms(MOBILE, "x", msg91_template_id="t1", settings=settings())
		self.assertFalse(result.ok)
		self.assertFalse(result.retryable)
		self.assertNotIn("secret-key", result.error)

	def test_network_errors_are_retryable(self):
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.post",
			side_effect=requests.ConnectTimeout("timed out"),
		):
			result = sms.send_sms(MOBILE, "x", msg91_template_id="t1", settings=settings())
		self.assertFalse(result.ok)
		self.assertTrue(result.retryable)

	def test_missing_template_id_fails_without_calling_msg91(self):
		with patch("hostel360.hostel_communication.providers.sms.requests.post") as post:
			result = sms.send_sms(MOBILE, "x", settings=settings())
		post.assert_not_called()
		self.assertFalse(result.ok)
		self.assertFalse(result.retryable)

	def test_disabled_and_test_mode(self):
		with patch("hostel360.hostel_communication.providers.sms.requests.post") as post:
			disabled = sms.send_sms(MOBILE, "x", msg91_template_id="t", settings=settings(sms_enabled=0))
			test_mode = sms.send_sms(MOBILE, "x", msg91_template_id="t", settings=settings(test_mode=1))
		post.assert_not_called()
		self.assertTrue(disabled.skipped)
		self.assertFalse(disabled.ok)
		self.assertTrue(test_mode.skipped)
		self.assertTrue(test_mode.ok)

	def test_alert_sms_splits_text_into_dlt_variables(self):
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.post",
			return_value=FakeResponse(200, {"type": "success", "message": "r1"}),
		) as post:
			sms.send_alert_sms(
				MOBILE,
				"CRITICAL",
				"Disk usage on hostel360-app-1 has reached 96 percent of capacity",
				settings=settings(alert_sms_template_id="alert-flow"),
			)
		recipient = post.call_args.kwargs["json"]["recipients"][0]
		self.assertEqual(post.call_args.kwargs["json"]["template_id"], "alert-flow")
		self.assertTrue(recipient["VAR1"].startswith("CRITICAL Disk usage"))
		self.assertTrue(all(len(recipient[f"VAR{i}"]) <= 30 for i in range(1, 4)))


class TestGenericHTTP(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		patcher = patch("hostel360.hostel_communication.providers.sms.get_secret", return_value="vendor-key")
		patcher.start()
		self.addCleanup(patcher.stop)

	def generic(self, **values):
		return settings(
			sms_provider="Generic HTTP",
			sms_api_url="https://sms.vendor.example/api/send",
			sms_param_mobile="to",
			sms_param_message="text",
			sms_param_sender="from",
			sms_param_template_id="dlt_te_id",
			sms_param_entity_id="pe_id",
			sms_extra_params='{"route": "transactional"}',
			sms_mobile_format="+91XXXXXXXXXX",
			**values,
		)

	def test_json_post_with_auth_header(self):
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.request",
			return_value=FakeResponse(200, {"status": "ok", "message_id": "abc-1"}),
		) as request:
			result = sms.send_sms(
				MOBILE,
				"Hello",
				dlt_template_id="1207160000000000999",
				settings=self.generic(sms_auth_header="Authorization"),
			)
		self.assertTrue(result.ok)
		self.assertEqual(result.message_id, "abc-1")
		kwargs = request.call_args.kwargs
		self.assertEqual(kwargs["method"], "POST")
		self.assertEqual(kwargs["headers"]["Authorization"], "vendor-key")
		self.assertEqual(
			kwargs["json"],
			{
				"route": "transactional",
				"to": "+919876543210",
				"text": "Hello",
				"from": "ACGCET",
				"dlt_te_id": "1207160000000000999",
				"pe_id": "1201160000000000001",
			},
		)

	def test_get_with_key_as_parameter_and_success_keyword(self):
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.request",
			return_value=FakeResponse(200, text="ERROR: invalid sender"),
		) as request:
			result = sms.send_sms(
				MOBILE,
				"Hello",
				settings=self.generic(
					sms_http_method="GET", sms_auth_param="key", sms_success_keyword="SUCCESS"
				),
			)
		self.assertFalse(result.ok)
		params = request.call_args.kwargs["params"]
		self.assertEqual(params["key"], "vendor-key")
		self.assertEqual(params["to"], "+919876543210")

	def test_form_post(self):
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.request",
			return_value=FakeResponse(200, text="SUCCESS 12345"),
		) as request:
			result = sms.send_sms(MOBILE, "Hello", settings=self.generic(sms_payload_format="Form"))
		self.assertTrue(result.ok)
		self.assertIn("data", request.call_args.kwargs)
		self.assertNotIn("json", request.call_args.kwargs)

	def test_unicode_parameter_only_for_unicode_text(self):
		config = self.generic(sms_param_unicode="type", sms_unicode_value="unicode")
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.request",
			return_value=FakeResponse(200, {"message_id": "u-1"}),
		) as request:
			sms.send_sms(MOBILE, "வணக்கம்", settings=config)
			self.assertEqual(request.call_args.kwargs["json"]["type"], "unicode")
			sms.send_sms(MOBILE, "Hello", settings=config)
			self.assertNotIn("type", request.call_args.kwargs["json"])
			# Without a configured parameter the gateway is left to detect Unicode itself.
			sms.send_sms(MOBILE, "வணக்கம்", settings=self.generic())
			self.assertNotIn("unicode", request.call_args.kwargs["json"])

	def test_server_errors_are_retryable(self):
		with patch(
			"hostel360.hostel_communication.providers.sms.requests.request",
			return_value=FakeResponse(502, text="Bad gateway"),
		):
			result = sms.send_sms(MOBILE, "Hello", settings=self.generic())
		self.assertFalse(result.ok)
		self.assertTrue(result.retryable)


class TestWhatsApp(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		patcher = patch(
			"hostel360.hostel_communication.providers.whatsapp.get_secret", return_value="EAAG-token"
		)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_template_message_payload(self):
		response = FakeResponse(
			200,
			{
				"messaging_product": "whatsapp",
				"contacts": [{"input": "919876543210", "wa_id": "919876543210"}],
				"messages": [{"id": "wamid.HBgMOTE5ODc2NTQzMjEwFQIAERgS"}],
			},
		)
		with patch(
			"hostel360.hostel_communication.providers.whatsapp.requests.post", return_value=response
		) as post:
			result = whatsapp.send_template(
				MOBILE,
				"h360_fee_due_published",
				"en",
				["Meena", "Rs.45,250", "line one\nline two", ""],
				settings=settings(),
			)
		self.assertTrue(result.ok)
		self.assertEqual(result.message_id, "wamid.HBgMOTE5ODc2NTQzMjEwFQIAERgS")
		self.assertEqual(post.call_args.args[0], "https://graph.facebook.com/v21.0/106540352242922/messages")
		self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer EAAG-token")
		payload = post.call_args.kwargs["json"]
		self.assertEqual(payload["messaging_product"], "whatsapp")
		self.assertEqual(payload["to"], "919876543210")
		self.assertEqual(payload["type"], "template")
		self.assertEqual(payload["template"]["name"], "h360_fee_due_published")
		self.assertEqual(payload["template"]["language"], {"code": "en"})
		body = payload["template"]["components"][0]
		self.assertEqual(body["type"], "body")
		# New lines are not allowed in parameters and empty ones are rejected by Meta.
		self.assertEqual(
			[p["text"] for p in body["parameters"]], ["Meena", "Rs.45,250", "line one line two", "-"]
		)

	def test_authentication_template_has_copy_code_button(self):
		with patch(
			"hostel360.hostel_communication.providers.whatsapp.requests.post",
			return_value=FakeResponse(200, {"messages": [{"id": "wamid.otp"}]}),
		) as post:
			result = whatsapp.send_authentication_code(
				MOBILE, "h360_otp_login", "en", "482913", settings=settings()
			)
		self.assertTrue(result.ok)
		components = post.call_args.kwargs["json"]["template"]["components"]
		self.assertEqual(components[0], {"type": "body", "parameters": [{"type": "text", "text": "482913"}]})
		self.assertEqual(
			components[1],
			{
				"type": "button",
				"sub_type": "url",
				"index": "0",
				"parameters": [{"type": "text", "text": "482913"}],
			},
		)

	def test_graph_errors(self):
		cases = (
			(400, 132001, False),  # template does not exist
			(400, 131026, False),  # undeliverable
			(400, 130429, True),  # rate limit
			(500, 1, True),
		)
		for status, code, retryable in cases:
			response = FakeResponse(
				status, {"error": {"message": "Problem", "code": code, "fbtrace_id": "A1"}}
			)
			with patch(
				"hostel360.hostel_communication.providers.whatsapp.requests.post", return_value=response
			):
				result = whatsapp.send_template(MOBILE, "h360_x", "en", ["a"], settings=settings())
			self.assertFalse(result.ok)
			self.assertEqual(result.retryable, retryable, code)
			self.assertIn(str(code), result.error)
			self.assertNotIn("EAAG-token", result.error)

	def test_missing_configuration(self):
		with patch("hostel360.hostel_communication.providers.whatsapp.requests.post") as post:
			no_template = whatsapp.send_template(MOBILE, "", "en", [], settings=settings())
			disabled = whatsapp.send_template(
				MOBILE, "h360_x", "en", [], settings=settings(whatsapp_enabled=0)
			)
			test_mode = whatsapp.send_template(MOBILE, "h360_x", "en", [], settings=settings(test_mode=1))
		post.assert_not_called()
		self.assertFalse(no_template.ok)
		self.assertTrue(disabled.skipped)
		self.assertTrue(test_mode.ok and test_mode.skipped)


class TestEmail(IntegrationTestCase):
	def test_queues_through_frappe_sendmail(self):
		with patch("frappe.sendmail", return_value=frappe._dict(name="EQ-0001")) as sendmail:
			result = email.send_email(
				"parent@example.com",
				"Fee due",
				"<p>Hello</p>",
				reference_doctype="Hostel Fee Invoice",
				reference_name="INV-1",
				settings=settings(),
			)
		self.assertTrue(result.ok)
		self.assertEqual(result.message_id, "EQ-0001")
		kwargs = sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], ["parent@example.com"])
		self.assertTrue(kwargs["delayed"])
		self.assertEqual(kwargs["reference_name"], "INV-1")

	def test_placeholder_and_invalid_addresses(self):
		with patch("frappe.sendmail") as sendmail:
			placeholder = email.send_email("x@portal.hostel360.invalid", "s", "b", settings=settings())
			invalid = email.send_email("not-an-address", "s", "b", settings=settings())
			disabled = email.send_email("a@example.com", "s", "b", settings=settings(email_enabled=0))
			test_mode = email.send_email("a@example.com", "s", "b", settings=settings(test_mode=1))
		sendmail.assert_not_called()
		self.assertTrue(placeholder.skipped)
		self.assertFalse(invalid.ok)
		self.assertTrue(disabled.skipped)
		self.assertTrue(test_mode.ok and test_mode.skipped)

	def test_queue_errors_are_retryable(self):
		with patch("frappe.sendmail", side_effect=frappe.OutgoingEmailError("no account")):
			result = email.send_email("a@example.com", "s", "b", settings=settings())
		self.assertFalse(result.ok)
		self.assertTrue(result.retryable)


class TestEmailNow(IntegrationTestCase):
	"""The direct path sign-in codes take: no Email Queue, a short timeout."""

	def account(self, **values):
		doc = frappe.new_doc("Email Account")
		doc.update(
			{
				"email_account_name": "Hostel Office",
				"email_id": "hostel.office@example.com",
				"smtp_server": "smtp.example.com",
				"enable_outgoing": 1,
				**values,
			}
		)
		return doc

	def test_message_skips_the_email_queue(self):
		with (
			patch.object(email, "outgoing_smtp_account", return_value=self.account()),
			patch.object(email, "_transmit") as transmit,
			patch("frappe.sendmail") as sendmail,
		):
			result = email.send_email_now(
				"Parent@Example.com",
				"Sign-in code",
				"<p>Your code is 482913</p>",
				timeout=(2, 3),
				settings=settings(),
			)
		self.assertTrue(result.ok)
		self.assertEqual(result.provider, email.DIRECT_PROVIDER)
		sendmail.assert_not_called()
		_account, _sender, to, message, timeout = transmit.call_args.args
		self.assertEqual((to, timeout), ("Parent@Example.com", 3.0))
		self.assertIn("482913", message)

	def test_refusals_before_any_connection(self):
		with (
			patch.object(email, "outgoing_smtp_account", return_value=self.account()) as account,
			patch.object(email, "_transmit") as transmit,
		):
			placeholder = email.send_email_now("x@portal.hostel360.invalid", "s", "b", settings=settings())
			invalid = email.send_email_now("not-an-address", "s", "b", settings=settings())
			disabled = email.send_email_now("a@example.com", "s", "b", settings=settings(email_enabled=0))
			test_mode = email.send_email_now("a@example.com", "s", "b", settings=settings(test_mode=1))
			with patch("frappe.are_emails_muted", return_value=True):
				muted = email.send_email_now("a@example.com", "s", "b", settings=settings())
			account.return_value = None
			no_account = email.send_email_now("a@example.com", "s", "b", settings=settings())
		transmit.assert_not_called()
		self.assertTrue(placeholder.skipped and not placeholder.ok)
		self.assertFalse(invalid.ok)
		self.assertTrue(disabled.skipped)
		self.assertTrue(test_mode.ok and test_mode.skipped)
		self.assertTrue(muted.skipped and not muted.ok)
		self.assertFalse(no_account.ok)
		self.assertIn("SMTP", no_account.error)

	def test_mail_server_trouble_is_a_retryable_failure_without_messages(self):
		before = list(frappe.local.message_log)

		def refuse(*args):
			frappe.throw("Invalid Outgoing Mail Server or Port: [Errno 111] Connection refused")

		with (
			patch.object(email, "outgoing_smtp_account", return_value=self.account()),
			patch.object(email, "_transmit", side_effect=refuse),
		):
			result = email.send_email_now("a@example.com", "s", "b", settings=settings())
		self.assertFalse(result.ok)
		self.assertTrue(result.retryable)
		self.assertIn("Connection refused", result.error)
		self.assertEqual(frappe.local.message_log, before)

	def test_only_smtp_accounts_send_codes(self):
		from frappe.email.doctype.email_account.email_account import EmailAccount

		for account, usable in (
			(self.account(), True),
			(self.account(service="Frappe Mail"), False),
			(self.account(smtp_server=""), False),
			(None, False),
		):
			with patch.object(EmailAccount, "find_outgoing", return_value=account):
				self.assertEqual(bool(email.outgoing_smtp_account()), usable)

	def test_transmit_uses_the_given_timeout(self):
		with (
			patch.object(frappe, "in_test", False),
			patch("frappe.email.smtp.SMTPServer") as server,
		):
			email._transmit(
				self.account(no_smtp_authentication=1), "office@example.com", "a@example.com", "message", 3.0
			)
		self.assertEqual(server.call_args.kwargs["timeout"], 3.0)
		self.assertEqual(server.call_args.kwargs["server"], "smtp.example.com")
		server.return_value.session.sendmail.assert_called_once_with(
			from_addr="office@example.com", to_addrs=["a@example.com"], msg=b"message"
		)
		server.return_value.quit.assert_called_once_with()


class TestProviderHealthAlerts(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.provider = f"Test Provider {frappe.generate_hash(length=6)}"
		self.addCleanup(
			frappe.cache.delete,
			base._failure_key(self.provider),
			base._breaker_key(self.provider),
			base._probe_key(self.provider),
		)

	def test_alert_after_repeated_failures_and_resolve_on_recovery(self):
		threshold = base._threshold()
		failure = DeliveryResult.failed(self.provider, "HTTP 503", retryable=True)
		with (
			patch("hostel360.hostel_operations.alerts.raise_alert") as raise_alert,
			patch("hostel360.hostel_operations.alerts.resolve_alert") as resolve_alert,
		):
			for _attempt in range(threshold - 1):
				base.record_outcome(failure)
			raise_alert.assert_not_called()

			base.record_outcome(failure)
			raise_alert.assert_called_once()
			kwargs = raise_alert.call_args.kwargs
			self.assertEqual(kwargs["category"], "Messaging")
			self.assertEqual(kwargs["severity"], "Warning")
			self.assertEqual(kwargs["key"], base._alert_key(self.provider))

			base.record_outcome(DeliveryResult.sent(self.provider, "ok"))
			resolve_alert.assert_called_once()
			self.assertEqual(resolve_alert.call_args.args[0], base._alert_key(self.provider))

	def test_skipped_messages_and_alert_messages_do_not_alert(self):
		with patch("hostel360.hostel_operations.alerts.raise_alert") as raise_alert:
			for _attempt in range(base._threshold() * 2):
				base.record_outcome(DeliveryResult.skip(self.provider, "test mode"))
				base.record_outcome(DeliveryResult.failed(self.provider, "down"), allow_alert=False)
		raise_alert.assert_not_called()

	def test_alerts_are_raised_after_the_sending_transaction(self):
		# A delivery job holds its transaction across slow provider calls; the Ops Alert is
		# created by a job of its own once that transaction has committed.
		threshold = base._threshold()
		failure = DeliveryResult.failed(self.provider, "HTTP 503", retryable=True)
		with (
			patch.object(frappe, "in_test", False),
			patch("frappe.enqueue") as enqueue,
			patch("hostel360.hostel_operations.alerts.raise_alert") as raise_alert,
		):
			for _attempt in range(threshold):
				base.record_outcome(failure)
			base.record_outcome(DeliveryResult.sent(self.provider, "ok"))
		raise_alert.assert_not_called()
		self.assertEqual(
			[call.args[0] for call in enqueue.call_args_list], [base.ALERT_METHOD, base.RESOLVE_METHOD]
		)
		kwargs = enqueue.call_args_list[0].kwargs
		self.assertTrue(kwargs["enqueue_after_commit"])
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(kwargs["queue"], "short")
		self.assertEqual((kwargs["provider"], kwargs["count"]), (self.provider, threshold))

	def test_deadlocks_in_alert_jobs_are_not_swallowed(self):
		# raise_alert / resolve_alert re-raise a deadlock only when InnoDB has already rolled
		# back the job's transaction; the job must fail rather than carry on.
		deadlock = frappe.QueryDeadlockError("Deadlock found when trying to get lock")
		with patch("hostel360.hostel_operations.alerts.raise_alert", side_effect=deadlock):
			with self.assertRaises(frappe.QueryDeadlockError):
				base.raise_provider_alert(self.provider, 5, 5, "HTTP 503")
		self.assertFalse(frappe.flags.hostel360_raising_messaging_alert)
		with patch("hostel360.hostel_operations.alerts.resolve_alert", side_effect=deadlock):
			with self.assertRaises(frappe.QueryDeadlockError):
				base.resolve_provider_alert(self.provider)

		# Anything else is still logged and swallowed.
		with patch("hostel360.hostel_operations.alerts.raise_alert", side_effect=RuntimeError("down")):
			base.raise_provider_alert(self.provider, 5, 5, "HTTP 503")
		with patch("hostel360.hostel_operations.alerts.resolve_alert", side_effect=RuntimeError("down")):
			base.resolve_provider_alert(self.provider)
