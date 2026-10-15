# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import json
import random
import sys
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime
from rq.timeouts import JobTimeoutException

from hostel360.hostel_communication import dispatch
from hostel360.hostel_communication import events as ev
from hostel360.hostel_communication.providers import base
from hostel360.hostel_communication.providers.base import DeliveryResult
from hostel360.setup.roles import DEPUTY_WARDEN_ROLE, MESS_MANAGER_ROLE, STUDENT_ROLE, WARDEN_ROLE

FEE_CONTEXT = {
	"student_name": "Meena S",
	"invoice": "INV-2026-09999",
	"invoice_type": "Hostel Fee",
	"amount": 45250,
	"due_date": "2026-11-15",
}


def random_mobile() -> str:
	return random.choice("6789") + "".join(random.choice("0123456789") for _ in range(9))


def make_user(*roles: str) -> str:
	email = f"msg-{frappe.generate_hash(length=8)}@example.com"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Message",
			"last_name": "Tester",
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	return user.name


class DispatchTestCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.settings = frappe.get_doc("Messaging Settings")
		self.settings.update(
			{
				"sms_enabled": 1,
				"whatsapp_enabled": 1,
				"email_enabled": 1,
				"test_mode": 1,
				"prefer_whatsapp": 0,
				"sms_provider": "MSG91",
				"whatsapp_phone_number_id": "1234567890",
			}
		)
		patcher = patch("hostel360.hostel_communication.dispatch.get_settings", return_value=self.settings)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.reference = f"REF-{frappe.generate_hash(length=8)}"

	def set_template(self, name: str, field: str, value) -> None:
		"""Change a template for one test only (the class shares one transaction)."""
		previous = frappe.db.get_value("Message Template", name, field)
		frappe.db.set_value("Message Template", name, field, value)
		self.addCleanup(frappe.db.set_value, "Message Template", name, field, previous)

	def logs(self, **filters) -> list[frappe._dict]:
		return frappe.get_all(
			"Message Log",
			filters={"reference_name": self.reference, **filters},
			fields=["*"],
			order_by="creation asc",
		)

	def deliver(self, recipients, event=ev.FEE_DUE_PUBLISHED, context=None, channels=None):
		dispatch.deliver(
			event,
			recipients,
			context or FEE_CONTEXT,
			reference_doctype="Hostel Fee Invoice",
			reference_name=self.reference,
			channels=channels,
		)


class TestNotify(DispatchTestCase):
	def test_notify_is_silent_while_installing_migrating_or_importing(self):
		for flag in dispatch.SUPPRESS_FLAGS:
			with patch.dict(frappe.flags, {flag: True}), patch("frappe.enqueue") as enqueue:
				dispatch.notify(ev.FEE_DUE_PUBLISHED, [{"mobile": random_mobile()}], FEE_CONTEXT)
			enqueue.assert_not_called()

	def test_notify_queues_delivery_on_the_short_queue(self):
		recipients = [{"mobile": random_mobile(), "name": f"Student {i}"} for i in range(60)]
		with patch("frappe.enqueue") as enqueue:
			dispatch.notify(ev.FEE_DUE_PUBLISHED, recipients, FEE_CONTEXT, "Hostel Fee Invoice", "INV-1")
		self.assertEqual(enqueue.call_count, 3)
		args, kwargs = enqueue.call_args_list[0]
		self.assertEqual(args[0], dispatch.DELIVER_METHOD)
		self.assertEqual(kwargs["queue"], "short")
		self.assertEqual(kwargs["notification_event"], ev.FEE_DUE_PUBLISHED)
		self.assertNotIn("event", kwargs)
		self.assertEqual(len(kwargs["recipients"]), dispatch.RECIPIENTS_PER_JOB)
		self.assertEqual(kwargs["reference_name"], "INV-1")

	def test_unknown_events_and_empty_recipients_send_nothing(self):
		with patch("frappe.enqueue") as enqueue:
			dispatch.notify("no_such_event", [{"mobile": random_mobile()}], {})
			dispatch.notify(ev.OTP_LOGIN, [{"mobile": random_mobile()}], {"otp": "123456"})
			dispatch.notify(ev.FEE_DUE_PUBLISHED, [{"name": "No address"}], FEE_CONTEXT)
			dispatch.notify(
				ev.FEE_DUE_PUBLISHED, [{"mobile": random_mobile()}], FEE_CONTEXT, channels=["fax"]
			)
		enqueue.assert_not_called()

	def test_notify_never_raises_into_the_caller(self):
		with patch("frappe.enqueue", side_effect=RuntimeError("queue down")):
			dispatch.notify(ev.FEE_DUE_PUBLISHED, [{"mobile": random_mobile()}], FEE_CONTEXT)

	def test_notify_delivers_end_to_end_in_tests(self):
		user = make_user(STUDENT_ROLE)
		dispatch.notify(
			ev.FEE_DUE_PUBLISHED,
			[{"user": user, "name": "Meena S", "kind": "student"}],
			FEE_CONTEXT,
			"Hostel Fee Invoice",
			self.reference,
			channels=["In-App"],
		)
		self.assertEqual([log.channel for log in self.logs()], ["In-App"])


class TestDeliver(DispatchTestCase):
	def test_renders_every_enabled_channel(self):
		user = make_user(STUDENT_ROLE)
		mobile = random_mobile()
		self.deliver(
			[
				{
					"user": user,
					"mobile": mobile,
					"email": "meena.parent@example.com",
					"name": "Meena S",
					"kind": "student",
				}
			]
		)

		logs = {log.channel: log for log in self.logs()}
		self.assertEqual(set(logs), {"SMS", "WhatsApp", "Email", "In-App"})

		sms = logs["SMS"]
		self.assertEqual(sms.status, "Skipped")  # test mode
		self.assertIn("Rs.45,250", sms.message)
		self.assertIn("INV-2026-09999", sms.message)
		self.assertEqual(sms.recipient, f"{mobile[:2]}xxxxxx{mobile[-2:]}")
		self.assertNotIn(mobile, json.dumps(sms, default=str))
		self.assertEqual(sms.template, f"{ev.FEE_DUE_PUBLISHED}-SMS")

		email = logs["Email"]
		self.assertTrue(email.recipient.startswith("me"))
		self.assertNotIn("meena.parent", email.recipient)
		self.assertIn("₹45,250.00", email.subject)

		in_app = logs["In-App"]
		self.assertEqual(in_app.status, "Sent")
		notification = frappe.get_doc("Notification Log", in_app.provider_message_id)
		self.assertEqual(notification.for_user, user)
		self.assertEqual(notification.document_type, "Hostel Fee Invoice")
		self.assertEqual(notification.document_name, self.reference)
		self.assertEqual(notification.link, "/hostel/fees")
		self.assertIn("Fee due", notification.subject)

	def test_placeholder_email_addresses_are_never_used(self):
		self.deliver([{"email": "abc123@portal.hostel360.invalid", "name": "Meena S"}])
		self.assertEqual(self.logs(), [])

	def test_channels_argument_limits_delivery(self):
		user = make_user(STUDENT_ROLE)
		self.deliver(
			[{"user": user, "mobile": random_mobile(), "email": "x@example.com"}], channels=["In-App"]
		)
		self.assertEqual([log.channel for log in self.logs()], ["In-App"])

	def test_globally_disabled_channels_are_not_used(self):
		self.settings.sms_enabled = 0
		self.settings.whatsapp_enabled = 0
		self.deliver([{"mobile": random_mobile(), "email": "x@example.com"}])
		self.assertEqual([log.channel for log in self.logs()], ["Email"])

	def test_disabled_template_skips_its_channel(self):
		self.set_template(f"{ev.FEE_DUE_PUBLISHED}-WhatsApp", "enabled", 0)
		self.deliver([{"mobile": random_mobile()}])
		self.assertEqual([log.channel for log in self.logs()], ["SMS"])

	def test_otp_event_is_never_sent_by_email_or_in_app(self):
		templates = dispatch._templates_for(ev.OTP_LOGIN, None, self.settings)
		self.assertEqual(set(templates), {"SMS", "WhatsApp"})

	def test_template_errors_are_logged_not_raised(self):
		self.set_template(f"{ev.FEE_DUE_PUBLISHED}-SMS", "body", "Fee {{ amount | nosuchfilter }}")
		self.deliver([{"mobile": random_mobile()}], channels=["SMS"])
		(log,) = self.logs()
		self.assertEqual(log.status, "Failed")
		self.assertTrue(log.error.startswith("Template error"))

	def test_shared_phone_gets_one_copy(self):
		mobile = random_mobile()
		self.deliver(
			[
				{"mobile": mobile, "name": "Student", "kind": "student"},
				{"mobile": mobile, "name": "Parent", "kind": "parent"},
			],
			channels=["SMS"],
		)
		self.assertEqual(len(self.logs()), 1)

	def test_html_channels_escape_student_text(self):
		user = make_user(STUDENT_ROLE)
		context = {**FEE_CONTEXT, "student_name": "<script>alert(1)</script>"}
		self.deliver([{"user": user}], context=context, channels=["In-App"])
		(log,) = self.logs()
		notification = frappe.get_doc("Notification Log", log.provider_message_id)
		self.assertNotIn("<script>", notification.description or "")
		self.assertIn("&lt;script&gt;", notification.description)

	def test_prefer_whatsapp_falls_back_to_sms(self):
		self.settings.prefer_whatsapp = 1
		self.settings.test_mode = 0
		failed = DeliveryResult.failed("WhatsApp Cloud API", "WhatsApp error 131026: undeliverable")
		with (
			patch("hostel360.hostel_communication.providers.whatsapp.send_template", return_value=failed),
			patch(
				"hostel360.hostel_communication.providers.sms.send_sms",
				return_value=DeliveryResult.sent("MSG91", "req-1"),
			) as send_sms,
		):
			self.deliver([{"mobile": random_mobile()}], channels=["SMS", "WhatsApp"])
		logs = {log.channel: log for log in self.logs()}
		self.assertEqual(logs["WhatsApp"].status, "Failed")
		self.assertIsNone(logs["WhatsApp"].payload)
		self.assertEqual(logs["SMS"].status, "Sent")
		self.assertEqual(logs["SMS"].provider_message_id, "req-1")
		send_sms.assert_called_once()

	def test_prefer_whatsapp_does_not_send_sms_when_whatsapp_works(self):
		self.settings.prefer_whatsapp = 1
		self.settings.test_mode = 0
		with (
			patch(
				"hostel360.hostel_communication.providers.whatsapp.send_template",
				return_value=DeliveryResult.sent("WhatsApp Cloud API", "wamid.1"),
			),
			patch("hostel360.hostel_communication.providers.sms.send_sms") as send_sms,
		):
			self.deliver([{"mobile": random_mobile()}], channels=["SMS", "WhatsApp"])
		send_sms.assert_not_called()
		self.assertEqual([log.channel for log in self.logs()], ["WhatsApp"])


class TestRetries(DispatchTestCase):
	def setUp(self):
		super().setUp()
		self.settings.test_mode = 0

	def fail_once(self, retryable=True) -> frappe._dict:
		result = DeliveryResult.failed("MSG91", "HTTP 503: busy", retryable=retryable)
		with patch("hostel360.hostel_communication.providers.sms.send_sms", return_value=result):
			self.deliver([{"mobile": self.mobile}], channels=["SMS"])
		(log,) = self.logs()
		return log

	@property
	def mobile(self):
		if not hasattr(self, "_mobile"):
			self._mobile = random_mobile()
		return self._mobile

	def test_failed_message_is_scheduled_with_encrypted_payload(self):
		log = self.fail_once()
		self.assertEqual(log.status, "Failed")
		self.assertEqual(log.attempts, 1)
		self.assertIsNotNone(log.next_retry_on)
		minutes = (get_datetime(log.next_retry_on) - now_datetime()).total_seconds() / 60
		self.assertAlmostEqual(minutes, dispatch.RETRY_BASE_MINUTES, delta=2)
		self.assertNotIn(self.mobile, log.payload)

	def test_non_retryable_failure_is_not_scheduled(self):
		log = self.fail_once(retryable=False)
		self.assertIsNone(log.payload)
		self.assertIsNone(log.next_retry_on)

	def test_backoff_and_give_up_after_three_attempts(self):
		log = self.fail_once()
		failure = DeliveryResult.failed("MSG91", "HTTP 503: busy", retryable=True)
		with patch("hostel360.hostel_communication.providers.sms.send_sms", return_value=failure) as send_sms:
			dispatch.retry_message(log.name)
			second = frappe.db.get_value(
				"Message Log", log.name, ["attempts", "next_retry_on", "payload"], as_dict=True
			)
			self.assertEqual(second.attempts, 2)
			minutes = (get_datetime(second.next_retry_on) - now_datetime()).total_seconds() / 60
			self.assertAlmostEqual(minutes, dispatch.RETRY_BASE_MINUTES * 2, delta=2)

			dispatch.retry_message(log.name)
			third = frappe.db.get_value(
				"Message Log", log.name, ["status", "attempts", "next_retry_on", "payload"], as_dict=True
			)
			self.assertEqual((third.status, third.attempts), ("Failed", 3))
			self.assertIsNone(third.payload)
			self.assertIsNone(third.next_retry_on)

			self.assertIsNone(dispatch.retry_message(log.name))
		self.assertEqual(send_sms.call_count, 2)
		self.assertEqual(send_sms.call_args.args[0], self.mobile)

	def test_scheduled_retry_sends_due_messages(self):
		log = self.fail_once()
		frappe.db.set_value("Message Log", log.name, "next_retry_on", add_to_date(now_datetime(), minutes=-1))
		with patch(
			"hostel360.hostel_communication.providers.sms.send_sms",
			return_value=DeliveryResult.sent("MSG91", "req-9"),
		):
			dispatch.retry_failed_messages()
		row = frappe.db.get_value(
			"Message Log", log.name, ["status", "attempts", "payload", "sent_on"], as_dict=True
		)
		self.assertEqual((row.status, row.attempts), ("Sent", 2))
		self.assertIsNone(row.payload)
		self.assertIsNotNone(row.sent_on)

	def test_messages_not_yet_due_are_left_alone(self):
		log = self.fail_once()
		with patch("hostel360.hostel_communication.providers.sms.send_sms") as send_sms:
			dispatch.retry_failed_messages()
		send_sms.assert_not_called()
		self.assertEqual(frappe.db.get_value("Message Log", log.name, "attempts"), 1)


class TestOtpAndDirectMessages(DispatchTestCase):
	def test_otp_message_never_stores_the_code(self):
		mobile = random_mobile()
		self.assertTrue(dispatch.send_otp_message(mobile, "482913", "sms"))
		log = frappe.get_all(
			"Message Log",
			filters={"event": ev.OTP_LOGIN, "recipient": f"{mobile[:2]}xxxxxx{mobile[-2:]}"},
			fields=["*"],
			order_by="creation desc",
			limit=1,
		)[0]
		self.assertNotIn("482913", json.dumps(log, default=str))
		self.assertIsNone(log.payload)

	def test_otp_provider_errors_are_scrubbed(self):
		self.settings.test_mode = 0
		mobile = random_mobile()
		echo = DeliveryResult.failed("MSG91", "rejected message '482913 is your OTP'", retryable=True)
		with patch("hostel360.hostel_communication.providers.sms.send_sms", return_value=echo):
			self.assertFalse(dispatch.send_otp_message(mobile, "482913", "SMS"))
		log = frappe.get_all(
			"Message Log",
			filters={"event": ev.OTP_LOGIN, "recipient": f"{mobile[:2]}xxxxxx{mobile[-2:]}"},
			fields=["error", "status", "payload"],
			limit=1,
		)[0]
		self.assertEqual(log.status, "Failed")
		self.assertNotIn("482913", log.error)
		self.assertIn("******", log.error)
		self.assertIsNone(log.payload)

	def test_otp_message_needs_a_mobile_channel(self):
		self.assertFalse(dispatch.send_otp_message(random_mobile(), "482913", "email"))
		self.assertFalse(dispatch.send_otp_message("12345", "482913", "sms"))

	def test_whatsapp_otp_uses_the_authentication_template(self):
		self.settings.test_mode = 0
		with patch(
			"hostel360.hostel_communication.providers.whatsapp.send_authentication_code",
			return_value=DeliveryResult.sent("WhatsApp Cloud API", "wamid.2"),
		) as send:
			self.assertTrue(dispatch.send_otp_message(random_mobile(), "135790", "whatsapp"))
		self.assertEqual(send.call_args.args[1], "h360_otp_login")
		self.assertEqual(send.call_args.args[3], "135790")

	def test_send_direct_logs_without_a_template(self):
		mobile = random_mobile()
		self.assertTrue(dispatch.send_direct("SMS", mobile, "Disk 91%", "Disk usage on app-1 is 91%."))
		log = frappe.get_all(
			"Message Log",
			filters={"event": ev.DIRECT_MESSAGE, "recipient": f"{mobile[:2]}xxxxxx{mobile[-2:]}"},
			fields=["channel", "status", "subject", "template"],
			limit=1,
		)[0]
		self.assertEqual((log.channel, log.status, log.subject), ("SMS", "Skipped", "Disk 91%"))
		self.assertIsNone(log.template)
		self.assertFalse(dispatch.send_direct("Fax", mobile, "x", "y"))
		self.assertFalse(dispatch.send_direct("Email", "ops@portal.hostel360.invalid", "x", "y"))


def smtp_account():
	"""An outgoing SMTP account as the e-mail provider sees it (never saved, never connected)."""
	account = frappe.new_doc("Email Account")
	account.update(
		{
			"email_account_name": "Hostel Office",
			"email_id": "hostel.office@example.com",
			"smtp_server": "smtp.example.com",
			"smtp_port": 587,
			"use_tls": 1,
			"enable_outgoing": 1,
			"default_outgoing": 1,
		}
	)
	return account


def html_part(message: str) -> str:
	import email
	from email import policy

	parsed = email.message_from_string(message, policy=policy.default)
	return parsed.get_body(preferencelist=("html",)).get_content()


class TestEmailedSignInCodes(DispatchTestCase):
	"""Sign-in codes by e-mail go straight to the mail server and are never stored."""

	def setUp(self):
		super().setUp()
		# Masked addresses keep the domain, so a domain of its own keeps each test's logs apart.
		self.address = f"meena@{frappe.generate_hash(length=8)}.example.com"
		for target, kwargs in (
			(
				"hostel360.hostel_communication.providers.email.outgoing_smtp_account",
				{"return_value": smtp_account()},
			),
			("hostel360.hostel_communication.providers.email._transmit", {}),
		):
			patcher = patch(target, **kwargs)
			setattr(self, target.rsplit(".", 1)[1].lstrip("_"), patcher.start())
			self.addCleanup(patcher.stop)

	def code_logs(self, address: str | None = None) -> list[frappe._dict]:
		from hostel360.utils.validation import mask_email

		return frappe.get_all(
			"Message Log",
			filters={
				"event": ev.OTP_LOGIN,
				"channel": "Email",
				"recipient": mask_email(address or self.address),
			},
			fields=["*"],
			order_by="creation asc",
		)

	def test_code_is_never_stored(self):
		queued = frappe.db.count("Email Queue")
		self.assertTrue(dispatch.send_otp_email(self.address, "482913"))
		(log,) = self.code_logs()
		self.assertEqual((log.status, log.template), ("Skipped", f"{ev.OTP_LOGIN}-Email"))
		self.assertNotIn("482913", json.dumps(log, default=str))
		self.assertIsNone(log.payload)
		self.assertFalse(log.subject)
		self.assertEqual(frappe.db.count("Email Queue"), queued)
		self.transmit.assert_not_called()

	def test_message_goes_straight_to_the_mail_server(self):
		self.settings.test_mode = 0
		queued = frappe.db.count("Email Queue")
		self.assertTrue(
			dispatch.send_otp_email(self.address.upper(), "482913", timeout=(2.0, 3.0), name="Meena S")
		)
		_account, sender, to, message, timeout = self.transmit.call_args.args
		self.assertEqual((sender, to, timeout), ("hostel.office@example.com", self.address, 3.0))
		import email

		headers = email.message_from_string(message)
		self.assertEqual(headers["To"], self.address)
		self.assertTrue(headers["Message-Id"])
		self.assertNotIn("482913", headers["Subject"])
		body = html_part(message)
		self.assertIn("482913", body)
		self.assertIn("Dear Meena S", body)
		self.assertIn("5 minutes", body)
		for placeholder in ("<!--recipient-->", "<!--email_open_check-->", "<!--unsubscribe_url-->"):
			self.assertNotIn(placeholder, message)

		(log,) = self.code_logs()
		self.assertEqual((log.status, log.provider), ("Sent", "SMTP"))
		self.assertNotIn("482913", json.dumps(log, default=str))
		self.assertEqual(frappe.db.count("Email Queue"), queued)

	def test_code_email_in_tamil(self):
		self.settings.test_mode = 0
		self.assertTrue(dispatch.send_otp_email(self.address, "482913", language="ta"))
		body = html_part(self.transmit.call_args.args[3])
		self.assertIn("482913", body)
		self.assertIn("யாரிடமும் இதைப் பகிர வேண்டாம்", body)
		# No name, no greeting: the code may be for a student or a parent.
		self.assertNotIn("அன்புள்ள", body)
		self.assertEqual(self.code_logs()[0].template, f"{ev.OTP_LOGIN}-Email-ta")

	def test_placeholder_and_invalid_addresses_are_refused(self):
		self.settings.test_mode = 0
		placeholder = f"ward@{frappe.generate_hash(length=8)}.portal.hostel360.invalid"
		for address in (placeholder, "not-an-address", ""):
			self.assertFalse(dispatch.send_otp_email(address, "482913"))
		self.assertFalse(dispatch.send_otp_email(self.address, ""))
		self.transmit.assert_not_called()
		self.assertEqual(self.code_logs(placeholder), [])
		self.assertEqual(self.code_logs(), [])

	def test_mail_server_errors_are_scrubbed_and_kept_from_the_caller(self):
		self.settings.test_mode = 0
		before = list(frappe.local.message_log)

		def refuse(*args):
			frappe.throw("Invalid Outgoing Mail Server or Port: message 482913 was refused")

		self.transmit.side_effect = refuse
		self.assertFalse(dispatch.send_otp_email(self.address, "482913"))
		self.assertEqual(frappe.local.message_log, before)
		(log,) = self.code_logs()
		self.assertEqual((log.status, log.provider), ("Failed", "SMTP"))
		self.assertIn("******", log.error)
		self.assertNotIn("482913", log.error)
		self.assertIsNone(log.payload)

	def test_switched_off_email_or_template_sends_nothing(self):
		self.settings.test_mode = 0
		self.set_template(f"{ev.OTP_LOGIN}-Email", "enabled", 0)
		self.assertFalse(dispatch.send_otp_email(self.address, "482913"))
		self.set_template(f"{ev.OTP_LOGIN}-Email", "enabled", 1)
		self.settings.email_enabled = 0
		self.assertFalse(dispatch.send_otp_email(self.address, "482913"))
		self.transmit.assert_not_called()

	def test_failing_mail_server_is_skipped(self):
		self.settings.test_mode = 0
		with patch("hostel360.hostel_communication.dispatch.provider_paused", return_value=True):
			self.assertFalse(dispatch.send_otp_email(self.address, "482913"))
		self.transmit.assert_not_called()
		(log,) = self.code_logs()
		self.assertTrue(log.error.startswith("Not sent"))

	def test_notifications_never_email_a_sign_in_code(self):
		self.assertNotIn("Email", dispatch._templates_for(ev.OTP_LOGIN, None, self.settings))


class TestDeliveryTransactions(DispatchTestCase):
	"""A delivery job keeps what it has sent, and never swallows its own timeout."""

	def in_background_job(self):
		patchers = (
			patch.object(frappe, "in_test", False),
			patch.object(frappe.local, "job", frappe._dict(method=dispatch.DELIVER_METHOD), create=True),
			patch.object(frappe.db, "commit"),
		)
		mocks = [p.start() for p in patchers]
		for p in patchers:
			self.addCleanup(p.stop)
		return mocks[-1]

	def test_each_recipient_is_committed(self):
		commit = self.in_background_job()
		self.deliver([{"mobile": random_mobile()} for _i in range(3)], channels=["SMS"])
		self.assertEqual(commit.call_count, 3)
		self.assertEqual(len(self.logs()), 3)

	def test_nothing_is_committed_outside_a_delivery_job(self):
		with patch.object(frappe, "in_test", False), patch.object(frappe.db, "commit") as commit:
			self.deliver([{"mobile": random_mobile()}], channels=["SMS"])
			# Called from some other job, the transaction is that job's.
			with patch.object(frappe.local, "job", frappe._dict(method="some.other.job"), create=True):
				self.deliver([{"mobile": random_mobile()}], channels=["SMS"])
		commit.assert_not_called()

	def test_job_timeout_stops_the_batch_after_saving_progress(self):
		commit = self.in_background_job()
		calls = []

		def deliver_to(job, recipient):
			calls.append(recipient)
			if len(calls) == 2:
				raise JobTimeoutException("Task exceeded maximum timeout value (300 seconds)")

		with (
			patch.object(dispatch._Delivery, "deliver_to", autospec=True, side_effect=deliver_to),
			self.assertRaises(JobTimeoutException),
		):
			self.deliver([{"mobile": random_mobile()} for _i in range(4)], channels=["SMS"])
		self.assertEqual(len(calls), 2)
		self.assertEqual(commit.call_count, 2)

	def test_other_errors_are_logged_and_the_batch_continues(self):
		calls = []

		def deliver_to(job, recipient):
			calls.append(recipient)
			if len(calls) == 1:
				raise RuntimeError("template engine exploded")

		with (
			patch.object(dispatch._Delivery, "deliver_to", autospec=True, side_effect=deliver_to),
			patch("frappe.log_error") as log_error,
		):
			self.deliver([{"mobile": random_mobile()} for _i in range(3)], channels=["SMS"])
		self.assertEqual(len(calls), 3)
		log_error.assert_called_once()

	def test_retry_and_log_writing_do_not_swallow_timeouts(self):
		self.settings.test_mode = 0
		failure = DeliveryResult.failed("MSG91", "HTTP 503: busy", retryable=True)
		with patch("hostel360.hostel_communication.providers.sms.send_sms", return_value=failure):
			self.deliver([{"mobile": random_mobile()}], channels=["SMS"])
		(log,) = self.logs()
		with (
			patch(
				"hostel360.hostel_communication.dispatch._send_rendered",
				side_effect=JobTimeoutException("timeout"),
			),
			self.assertRaises(JobTimeoutException),
		):
			dispatch.retry_message(log.name)

		with (
			patch("frappe.model.document.Document.insert", side_effect=JobTimeoutException("timeout")),
			self.assertRaises(JobTimeoutException),
		):
			dispatch._finish_log(
				dispatch._new_log("SMS", ev.FEE_REMINDER, random_mobile(), None, None, None, None),
				DeliveryResult.sent("MSG91", "x"),
			)


class TestPayloadPurge(DispatchTestCase):
	def setUp(self):
		super().setUp()
		self.settings.test_mode = 0

	def failed_log(self) -> str:
		failure = DeliveryResult.failed("MSG91", "HTTP 503: busy", retryable=True)
		with patch("hostel360.hostel_communication.providers.sms.send_sms", return_value=failure):
			self.deliver([{"mobile": random_mobile()}], channels=["SMS"])
		return self.logs()[-1].name

	def test_only_messages_that_will_not_be_retried_lose_their_payload(self):
		waiting = self.failed_log()
		too_old = self.failed_log()
		used_up = self.failed_log()
		delivered = self.failed_log()
		frappe.db.set_value(
			"Message Log", too_old, "creation", add_to_date(now_datetime(), hours=-72), update_modified=False
		)
		frappe.db.set_value("Message Log", used_up, "attempts", dispatch.MAX_ATTEMPTS)
		frappe.db.set_value("Message Log", delivered, "status", "Sent")

		dispatch._purge_stale_payloads(add_to_date(now_datetime(), hours=-dispatch.RETRY_WINDOW_HOURS))

		rows = {
			row.name: row
			for row in frappe.get_all(
				"Message Log",
				filters={"name": ("in", [waiting, too_old, used_up, delivered])},
				fields=["name", "payload", "next_retry_on"],
			)
		}
		self.assertIsNotNone(rows[waiting].payload)
		self.assertIsNotNone(rows[waiting].next_retry_on)
		for name in (too_old, used_up, delivered):
			self.assertIsNone(rows[name].payload, name)
			self.assertIsNone(rows[name].next_retry_on, name)

	def test_purge_reads_candidates_through_the_retry_index(self):
		# The candidates come from a plain read filtered on the indexed next_retry_on column;
		# the update then touches only those rows, by name.
		self.failed_log()
		with patch("frappe.db.sql", wraps=frappe.db.sql) as sql:
			dispatch._purge_stale_payloads(add_to_date(now_datetime(), hours=-dispatch.RETRY_WINDOW_HOURS))
		statements = [str(call.args[0]) for call in sql.call_args_list]
		updates = [q for q in statements if q.lstrip().upper().startswith("UPDATE")]
		selects = [q for q in statements if q.lstrip().upper().startswith("SELECT")]
		self.assertTrue(any("next_retry_on" in q and "FOR UPDATE" not in q.upper() for q in selects))
		self.assertTrue(all("`name` IN" in q for q in updates))


class TestOtpCircuitBreaker(DispatchTestCase):
	"""Sign-in codes stop calling a provider that keeps failing, and recover on their own."""

	def setUp(self):
		super().setUp()
		self.settings.test_mode = 0
		self.provider = "MSG91"
		self.clear_provider_state()
		self.addCleanup(self.clear_provider_state)

	def clear_provider_state(self):
		frappe.cache.delete(
			base._failure_key(self.provider), base._breaker_key(self.provider), base._probe_key(self.provider)
		)

	def break_provider(self):
		failure = DeliveryResult.failed(self.provider, "ReadTimeout", retryable=True)
		with patch("hostel360.hostel_communication.providers.base.raise_provider_alert"):
			for _failure in range(base._threshold()):
				base.record_outcome(failure)

	def test_timeout_reaches_the_provider(self):
		with patch(
			"hostel360.hostel_communication.providers.sms.send_sms",
			return_value=DeliveryResult.sent(self.provider, "req-1"),
		) as send_sms:
			self.assertTrue(dispatch.send_otp_message(random_mobile(), "482913", "SMS", timeout=(2, 3)))
		self.assertEqual(send_sms.call_args.kwargs["timeout"], (2, 3))

	def test_open_breaker_lets_one_probe_through_then_skips(self):
		self.break_provider()
		failure = DeliveryResult.failed(self.provider, "ReadTimeout", retryable=True)
		with (
			patch("hostel360.hostel_communication.providers.sms.send_sms", return_value=failure) as send_sms,
			patch("hostel360.hostel_communication.providers.base.raise_provider_alert"),
		):
			self.assertFalse(dispatch.send_otp_message(random_mobile(), "482913", "SMS"))
			self.assertEqual(send_sms.call_count, 1)  # the probe
			skipped = random_mobile()
			self.assertFalse(dispatch.send_otp_message(skipped, "482913", "SMS"))
			self.assertEqual(send_sms.call_count, 1)
		log = frappe.get_all(
			"Message Log",
			filters={"event": ev.OTP_LOGIN, "recipient": f"{skipped[:2]}xxxxxx{skipped[-2:]}"},
			fields=["status", "error"],
		)[0]
		self.assertEqual(log.status, "Failed")
		self.assertIn("Not sent", log.error)

	def test_a_successful_send_closes_the_breaker(self):
		self.break_provider()
		self.assertTrue(base.provider_paused(self.provider) is False)  # the probe slot
		self.assertTrue(base.provider_paused(self.provider))
		base.record_outcome(DeliveryResult.sent(self.provider, "bg-1"))
		self.assertFalse(base.provider_paused(self.provider))

	def test_configuration_errors_do_not_open_the_breaker(self):
		failure = DeliveryResult.failed(self.provider, "Set the MSG91 Flow template ID")
		with patch("hostel360.hostel_communication.providers.base.raise_provider_alert"):
			for _failure in range(base._threshold() * 2):
				base.record_outcome(failure)
		self.assertFalse(base.provider_paused(self.provider))


class TestRecipients(IntegrationTestCase):
	def test_recipients_for_users(self):
		user = make_user(WARDEN_ROLE)
		mobile = random_mobile()
		frappe.db.set_value("User", user, "mobile_no", mobile)
		disabled = make_user(WARDEN_ROLE)
		frappe.db.set_value("User", disabled, "enabled", 0)
		recipients = dispatch.recipients_for_users([user, disabled, "Guest", user])
		self.assertEqual(len(recipients), 1)
		self.assertEqual(recipients[0]["user"], user)
		self.assertEqual(recipients[0]["mobile"], mobile)
		self.assertEqual(recipients[0]["email"], user)
		self.assertEqual(recipients[0]["kind"], "staff")

	def test_recipients_for_approvers_respects_hostel_scope(self):
		if not frappe.db.exists("DocType", "Hostel"):
			self.skipTest("Hostel Setup is not installed on this site")
		hostels = []
		for _i in range(2):
			code = f"T{frappe.generate_hash(length=5).upper()}"
			frappe.get_doc(
				{
					"doctype": "Hostel",
					"hostel_name": f"Test Hostel {code}",
					"hostel_code": code,
					"gender": "Male",
				}
			).insert(ignore_permissions=True)
			hostels.append(code)

		scoped_here = make_user(DEPUTY_WARDEN_ROLE)
		scoped_elsewhere = make_user(DEPUTY_WARDEN_ROLE)
		unrestricted = make_user(DEPUTY_WARDEN_ROLE)
		warden = make_user(WARDEN_ROLE)
		for user, hostel in ((scoped_here, hostels[0]), (scoped_elsewhere, hostels[1])):
			frappe.get_doc(
				{"doctype": "User Permission", "user": user, "allow": "Hostel", "for_value": hostel}
			).insert(ignore_permissions=True)

		users = {r["user"] for r in dispatch.recipients_for_approvers(DEPUTY_WARDEN_ROLE, hostels[0])}
		self.assertIn(scoped_here, users)
		self.assertIn(warden, users)
		self.assertNotIn(scoped_elsewhere, users)
		# A deputy warden with no hostel assigned is a misconfiguration, not "every hostel".
		self.assertNotIn(unrestricted, users)

		# Roles that are not hostel-scoped (mess managers) act for every hostel.
		mess_manager = make_user(MESS_MANAGER_ROLE)
		users = {r["user"] for r in dispatch.recipients_for_approvers(MESS_MANAGER_ROLE, hostels[0])}
		self.assertIn(mess_manager, users)

		everyone = {r["user"] for r in dispatch.recipients_for_approvers(DEPUTY_WARDEN_ROLE)}
		self.assertTrue({scoped_here, scoped_elsewhere, unrestricted, warden} <= everyone)

	def test_recipients_for_approvers_follow_block_scope(self):
		block_deputy = make_user(DEPUTY_WARDEN_ROLE)
		other_deputy = make_user(DEPUTY_WARDEN_ROLE)
		warden = make_user(WARDEN_ROLE)
		with patch(
			"hostel360.hostel_people.services.get_scoped_staff_users", return_value=[block_deputy]
		) as scoped:
			users = {
				r["user"] for r in dispatch.recipients_for_approvers(DEPUTY_WARDEN_ROLE, "H1", block="H1-A")
			}
		scoped.assert_called_once_with(DEPUTY_WARDEN_ROLE, "H1", "H1-A")
		self.assertIn(block_deputy, users)
		self.assertIn(warden, users)
		self.assertNotIn(other_deputy, users)

		# A student stands for their hostel (the last one once they have left) and block.
		with (
			patch("hostel360.hostel_people.services.get_student_hostel", return_value="H2"),
			patch("hostel360.hostel_people.services.get_student_block", return_value="H2-B"),
			patch("hostel360.hostel_people.services.get_scoped_staff_users", return_value=[]) as scoped,
		):
			dispatch.recipients_for_approvers(DEPUTY_WARDEN_ROLE, student="STU-1")
		scoped.assert_called_once_with(DEPUTY_WARDEN_ROLE, "H2", "H2-B")

	def test_recipients_for_approvers_fall_back_to_hostel_permissions(self):
		if not frappe.db.exists("DocType", "Hostel"):
			self.skipTest("Hostel Setup is not installed on this site")
		code = f"T{frappe.generate_hash(length=5).upper()}"
		frappe.get_doc(
			{"doctype": "Hostel", "hostel_name": f"Test Hostel {code}", "hostel_code": code, "gender": "Male"}
		).insert(ignore_permissions=True)
		scoped_here = make_user(DEPUTY_WARDEN_ROLE)
		frappe.get_doc(
			{"doctype": "User Permission", "user": scoped_here, "allow": "Hostel", "for_value": code}
		).insert(ignore_permissions=True)
		elsewhere = make_user(DEPUTY_WARDEN_ROLE)
		frappe.get_doc(
			{"doctype": "User Permission", "user": elsewhere, "allow": "Hostel", "for_value": "NOT-" + code}
		).insert(ignore_permissions=True, ignore_links=True)
		with patch.dict(sys.modules, {"hostel360.hostel_people.services": None}):
			users = {r["user"] for r in dispatch.recipients_for_approvers(DEPUTY_WARDEN_ROLE, code)}
		self.assertIn(scoped_here, users)
		self.assertNotIn(elsewhere, users)

	def test_recipients_for_student_and_guardians(self):
		if not frappe.db.exists("DocType", "Hostel Student"):
			self.skipTest("Hostel People is not installed on this site")
		guardians = []
		for status in ("Pending", "Rejected"):
			guardian = frappe.get_doc(
				{
					"doctype": "Hostel Guardian",
					"guardian_name": f"Guardian {status}",
					"relation": "Mother",
					"mobile_number": random_mobile(),
				}
			).insert(ignore_permissions=True)
			guardians.append(guardian)
		student = frappe.get_doc(
			{
				"doctype": "Hostel Student",
				"register_number": f"MSG{frappe.generate_hash(length=8).upper()}",
				"first_name": "Recipient",
				"last_name": "Student",
				"gender": "Female",
				"mobile_number": random_mobile(),
				"status": "Active",
				"guardians": [{"guardian": g.name, "relation": "Mother"} for g in guardians],
			}
		).insert(ignore_permissions=True)

		frappe.db.set_value("Hostel Guardian", guardians[1].name, "validation_status", "Rejected")

		only_student = dispatch.recipients_for_student(student.name)
		self.assertEqual(
			[(r["kind"], r["mobile"]) for r in only_student], [("student", student.mobile_number)]
		)

		everyone = dispatch.recipients_for_student(student.name, include_guardians=True)
		self.assertEqual([r["kind"] for r in everyone], ["student", "parent"])
		self.assertEqual(everyone[1]["mobile"], guardians[0].mobile_number)

		self.assertEqual(
			dispatch.recipients_for_student(student.name, include_student=False, include_guardians=True)[0][
				"name"
			],
			"Guardian Pending",
		)
		self.assertEqual(dispatch.recipients_for_student("NO-SUCH-STUDENT"), [])


def tamil_user(*roles: str) -> str:
	user = make_user(*roles)
	frappe.db.set_value("User", user, "language", ev.TAMIL, update_modified=False)
	return user


LEAVE_CONTEXT = {
	"student_name": "Meena S",
	"leave": "LV-2026-00078",
	"status": "Approved",
	"from_datetime": "2026-07-04 16:00:00",
	"to_datetime": "2026-07-07 19:00:00",
	"warden_remarks": "",
}


class TestLanguages(DispatchTestCase):
	def deliver_leave(self, recipients, channels=None):
		dispatch.deliver(
			ev.LEAVE_DECIDED,
			recipients,
			LEAVE_CONTEXT,
			reference_doctype="Home Visit Leave",
			reference_name=self.reference,
			channels=channels,
		)

	def notification_for(self, log) -> frappe._dict:
		return frappe.db.get_value(
			"Notification Log", log.provider_message_id, ["subject", "description"], as_dict=True
		)

	def test_each_recipient_gets_their_own_language(self):
		tamil, english = tamil_user(STUDENT_ROLE), make_user(STUDENT_ROLE)
		self.deliver_leave([{"user": tamil}, {"user": english}], channels=["In-App"])
		logs = {log.recipient_user: log for log in self.logs()}

		self.assertEqual(logs[tamil].template, f"{ev.LEAVE_DECIDED}-In-App-ta")
		subject = self.notification_for(logs[tamil]).subject
		self.assertIn("ஒப்புதல் பெற்றது", subject)
		self.assertIn("Meena S", subject)

		self.assertEqual(logs[english].template, f"{ev.LEAVE_DECIDED}-In-App")
		self.assertEqual(self.notification_for(logs[english]).subject, "Leave approved for Meena S")

	def test_language_given_with_the_recipient_wins(self):
		user = make_user(STUDENT_ROLE)
		self.deliver(
			[{"user": user, "email": "meena.parent@example.com", "language": "ta-IN"}],
			channels=["Email", "In-App"],
		)
		self.assertEqual(
			{log.channel: log.template for log in self.logs()},
			{"Email": f"{ev.FEE_DUE_PUBLISHED}-Email-ta", "In-App": f"{ev.FEE_DUE_PUBLISHED}-In-App-ta"},
		)

	def test_people_without_an_account_get_english(self):
		self.deliver([{"mobile": random_mobile(), "email": "parent@example.com"}], channels=["Email"])
		(log,) = self.logs()
		self.assertEqual(log.template, f"{ev.FEE_DUE_PUBLISHED}-Email")

	def test_disabled_translation_falls_back_to_english(self):
		self.set_template(f"{ev.LEAVE_DECIDED}-In-App-ta", "enabled", 0)
		self.deliver_leave([{"user": tamil_user(STUDENT_ROLE)}], channels=["In-App"])
		(log,) = self.logs()
		self.assertEqual(log.template, f"{ev.LEAVE_DECIDED}-In-App")

	def test_english_template_is_the_switch_for_the_channel(self):
		self.set_template(f"{ev.LEAVE_DECIDED}-In-App", "enabled", 0)
		self.deliver_leave([{"user": tamil_user(STUDENT_ROLE)}], channels=["In-App"])
		self.assertEqual(self.logs(), [])

	def test_tamil_sms_only_once_enabled_and_registered(self):
		user, mobile = tamil_user(STUDENT_ROLE), random_mobile()
		english, tamil = f"{ev.LEAVE_DECIDED}-SMS", f"{ev.LEAVE_DECIDED}-SMS-ta"
		self.set_template(english, "dlt_template_id", "1207170000000000001")

		# Shipped disabled: English goes out.
		self.deliver_leave([{"user": user, "mobile": mobile}], channels=["SMS"])
		self.assertEqual(self.logs()[-1].template, english)

		# Enabled but not registered on DLT while the English one is: still English.
		self.set_template(tamil, "enabled", 1)
		self.deliver_leave([{"user": user, "mobile": random_mobile()}], channels=["SMS"])
		self.assertEqual(self.logs()[-1].template, english)

		self.set_template(tamil, "dlt_template_id", "1207170000000000002")
		self.deliver_leave([{"user": user, "mobile": random_mobile()}], channels=["SMS"])
		log = self.logs()[-1]
		self.assertEqual(log.template, tamil)
		self.assertIn("ஒப்புதல் பெற்றது", log.message)

	def test_tamil_whatsapp_uses_the_tamil_template_version(self):
		self.settings.test_mode = 0
		self.set_template(f"{ev.LEAVE_DECIDED}-WhatsApp-ta", "enabled", 1)
		with patch(
			"hostel360.hostel_communication.providers.whatsapp.send_template",
			return_value=DeliveryResult.sent("WhatsApp Cloud API", "wamid.ta"),
		) as send:
			self.deliver_leave(
				[{"user": tamil_user(STUDENT_ROLE), "mobile": random_mobile()}], channels=["WhatsApp"]
			)
		name, language, parameters = send.call_args.args[1:4]
		self.assertEqual((name, language), ("h360_leave_decided", "ta"))
		self.assertIn("ஒப்புதல் பெற்றது", parameters)
		self.assertEqual(parameters[-1], "இல்லை")

	def test_english_dates_do_not_follow_the_job_language(self):
		# A delivery job takes on the language of the user who queued it.
		with patch.object(frappe.local, "lang", ev.TAMIL):
			self.deliver_leave([{"user": make_user(STUDENT_ROLE)}], channels=["In-App"])
		(log,) = self.logs()
		self.assertEqual(log.template, f"{ev.LEAVE_DECIDED}-In-App")
		self.assertIn("04-07-2026 04:00 PM", log.message)

	def test_tamil_sign_in_code_when_enabled(self):
		mobile = random_mobile()
		self.assertTrue(dispatch.send_otp_message(mobile, "482913", "SMS", language="ta"))
		self.set_template(f"{ev.OTP_LOGIN}-SMS-ta", "enabled", 1)
		self.assertTrue(dispatch.send_otp_message(mobile, "482913", "SMS", language="ta"))
		self.assertTrue(dispatch.send_otp_message(mobile, "482913", "SMS", language="en"))
		templates = frappe.get_all(
			"Message Log",
			filters={"event": ev.OTP_LOGIN, "recipient": f"{mobile[:2]}xxxxxx{mobile[-2:]}"},
			order_by="creation asc",
			pluck="template",
		)
		self.assertEqual(templates, [f"{ev.OTP_LOGIN}-SMS", f"{ev.OTP_LOGIN}-SMS-ta", f"{ev.OTP_LOGIN}-SMS"])

	def test_tamil_sign_in_code_is_rendered_in_tamil(self):
		self.settings.test_mode = 0
		self.set_template(f"{ev.OTP_LOGIN}-SMS-ta", "enabled", 1)
		with patch(
			"hostel360.hostel_communication.providers.sms.send_sms",
			return_value=DeliveryResult.sent("MSG91", "req-ta"),
		) as send_sms:
			self.assertTrue(dispatch.send_otp_message(random_mobile(), "482913", "SMS", language="ta"))
		body = send_sms.call_args.args[1]
		self.assertTrue(body.startswith("482913 - "))
		self.assertIn("யாரிடமும் பகிர வேண்டாம்", body)

	def test_recipient_language_is_normalised(self):
		(row,) = dispatch.clean_recipients([{"mobile": random_mobile(), "language": "TA"}])
		self.assertEqual(row["language"], ev.TAMIL)
		(row,) = dispatch.clean_recipients([{"mobile": random_mobile(), "language": "hi"}])
		self.assertEqual(row["language"], ev.ENGLISH)
		(row,) = dispatch.clean_recipients([{"mobile": random_mobile()}])
		self.assertIsNone(row["language"])


class TestReferenceContext(DispatchTestCase):
	def test_missing_values_come_from_the_referenced_document(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Gate register", "date": "2026-07-07"})
		todo.insert(ignore_permissions=True)
		spec = ("ToDo", ("description", "date", "no_such_field"))
		with patch.dict(ev.REFERENCE_FALLBACKS, {ev.LEAVE_RETURNED: spec}):
			filled = dispatch.complete_context(
				ev.LEAVE_RETURNED, {"student_name": "Meena S", "description": ""}, "ToDo", todo.name
			)
			self.assertEqual(filled["description"], "Gate register")
			self.assertEqual(filled["date"], "2026-07-07")
			self.assertEqual(filled["student_name"], "Meena S")

			# Values the caller gave are kept, and other documents are not read.
			kept = dispatch.complete_context(ev.LEAVE_RETURNED, {"description": "Mine"}, "ToDo", todo.name)
			self.assertEqual(kept["description"], "Mine")
			other = dispatch.complete_context(ev.LEAVE_RETURNED, {}, "Note", todo.name)
			self.assertEqual(other, {})
		# Events without fallbacks are passed through untouched.
		self.assertEqual(dispatch.complete_context(ev.LEAVE_DECIDED, {}, "ToDo", todo.name), {})

	def test_notify_completes_the_context_before_queueing(self):
		with (
			patch(
				"hostel360.hostel_communication.dispatch.complete_context", return_value={"x": 1}
			) as complete,
			patch("frappe.enqueue") as enqueue,
		):
			dispatch.notify(
				ev.LEAVE_CHECKED_OUT,
				[{"mobile": random_mobile()}],
				{"leave": "LV-1"},
				"Home Visit Leave",
				"LV-1",
			)
		complete.assert_called_once_with(ev.LEAVE_CHECKED_OUT, {"leave": "LV-1"}, "Home Visit Leave", "LV-1")
		self.assertEqual(enqueue.call_args.kwargs["context"], {"x": 1})

	def test_gate_messages_reach_guardians_in_their_language(self):
		guardian = tamil_user()
		context = {
			"student_name": "Meena S",
			"leave": "LV-2026-00078",
			"hostel": "GH1",
			"destination": "Madurai",
			"checked_out_on": "2026-07-04 16:20:00",
			"to_datetime": "2026-07-07 19:00:00",
			"returned_on": "2026-07-07 18:45:00",
		}
		for event in (ev.LEAVE_CHECKED_OUT, ev.LEAVE_RETURNED):
			dispatch.deliver(
				event,
				[{"user": guardian, "kind": "parent"}],
				context,
				reference_doctype="Home Visit Leave",
				reference_name=self.reference,
				channels=["In-App"],
			)
		checked_out, returned = self.logs()
		self.assertEqual(checked_out.template, f"{ev.LEAVE_CHECKED_OUT}-In-App-ta")
		self.assertIn("04-07-2026 04:20 PM", checked_out.message)
		self.assertEqual(returned.template, f"{ev.LEAVE_RETURNED}-In-App-ta")
		self.assertIn("07-07-2026 06:45 PM", returned.message)
		link = frappe.db.get_value("Notification Log", checked_out.provider_message_id, "link")
		self.assertEqual(link, "/hostel/leave")
