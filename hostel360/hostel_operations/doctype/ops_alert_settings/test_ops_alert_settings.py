# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from hostel360.hostel_operations import setup
from hostel360.hostel_operations.tests.helpers import (
	LEVEL_ONE_EMAIL,
	DeliveryRecorder,
	clear_settings_cache,
	configure_settings,
)


class TestOpsAlertSettings(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()

	def test_install_defaults_is_idempotent(self):
		settings = frappe.get_doc("Ops Alert Settings")
		settings.set("escalation_levels", [])
		settings.ingest_secret = ""
		settings.oci_notification_token = ""
		settings.flags.ignore_permissions = True
		settings.save()

		setup.install_defaults()
		settings = frappe.get_doc("Ops Alert Settings")
		levels = {row.level: row for row in settings.escalation_levels}
		self.assertEqual(sorted(levels), [1, 2, 3])
		self.assertEqual(levels[1].notify_emails, "support@hyfib.com")
		self.assertEqual(levels[1].notify_mobiles, "9606060596")
		self.assertEqual(levels[2].after_minutes, 120)
		self.assertEqual(levels[3].after_minutes, 180)
		self.assertEqual(levels[3].notify_emails, "manikandan.s@hyfib.com")
		self.assertEqual(levels[3].notify_mobiles, "9741380530")
		self.assertTrue(levels[3].outage_immediate)
		secret = settings.get_password("ingest_secret")
		token = settings.get_password("oci_notification_token")
		self.assertGreaterEqual(len(secret), 64)
		self.assertGreaterEqual(len(token), 32)

		setup.install_defaults()
		settings = frappe.get_doc("Ops Alert Settings")
		self.assertEqual(len(settings.escalation_levels), 3)
		self.assertEqual(settings.get_password("ingest_secret"), secret, "secrets are never rotated by setup")
		self.assertEqual(settings.get_password("oci_notification_token"), token)

	def test_channel_validation(self):
		settings = configure_settings()
		settings.append("channels", {"channel_type": "Email", "target": "not-an-email", "min_severity": "Info"})
		self.assertRaises(frappe.ValidationError, settings.save)

		settings = configure_settings()
		settings.append("channels", {"channel_type": "SMS", "target": "12345", "min_severity": "Critical"})
		self.assertRaises(frappe.ValidationError, settings.save)

		settings = configure_settings()
		settings.append(
			"channels",
			{"channel_type": "Microsoft Teams Webhook", "target": "Ops", "secret_url": "http://teams.example.com/hook"},
		)
		self.assertRaises(frappe.ValidationError, settings.save)

		settings = configure_settings()
		settings.append("channels", {"channel_type": "Google Chat Webhook", "target": "Ops"})
		self.assertRaises(frappe.ValidationError, settings.save)

	def test_mobile_numbers_are_normalised(self):
		settings = configure_settings()
		settings.append("channels", {"channel_type": "SMS", "target": "+91 96060 60596", "min_severity": "Critical"})
		settings.escalation_levels[0].notify_mobiles = "+91 97413 80530\n09606060596"
		settings.save()
		self.assertEqual(settings.channels[-1].target, "9606060596")
		self.assertEqual(settings.escalation_levels[0].notify_mobiles, "9741380530\n9606060596")

	def test_escalation_matrix_validation(self):
		settings = configure_settings()
		settings.escalation_levels[1].level = 1
		self.assertRaises(frappe.ValidationError, settings.save)

		settings = configure_settings()
		settings.escalation_levels[2].after_minutes = 60
		self.assertRaises(frappe.ValidationError, settings.save)

		settings = configure_settings()
		settings.escalation_levels[0].notify_emails = "helpdesk@@example"
		self.assertRaises(frappe.ValidationError, settings.save)

	def test_threshold_and_recipient_validation(self):
		settings = configure_settings()
		settings.disk_warning_percent = 95
		settings.disk_critical_percent = 90
		self.assertRaises(frappe.ValidationError, settings.save)

		settings = configure_settings()
		settings.heartbeat_url = "http://hc.example.com/ping/abc"
		self.assertRaises(frappe.ValidationError, settings.save)

		settings = configure_settings()
		settings.report_recipients = "principal@example.edu, warden@example.edu\nprincipal@example.edu"
		settings.save()
		self.assertEqual(settings.report_recipients, "principal@example.edu\nwarden@example.edu")

	def test_send_test_notification(self):
		settings = configure_settings()
		with DeliveryRecorder() as recorder:
			results = settings.send_test_notification()
		self.assertTrue(results)
		self.assertTrue(all(row["ok"] for row in results))
		self.assertIn(LEVEL_ONE_EMAIL, recorder.emails[0]["recipients"])
		self.assertEqual(recorder.emails[0]["message"]["event"], "test")
		self.assertTrue(recorder.webhooks, "the Slack channel takes critical messages")

	def test_actions_need_operations_role(self):
		settings = configure_settings()
		user = _plain_user()
		with self.set_user(user):
			self.assertRaises(frappe.PermissionError, settings.send_test_notification)
			self.assertRaises(frappe.PermissionError, settings.reveal_ingest_secret)
			self.assertRaises(frappe.PermissionError, settings.rotate_secret, "ingest_secret")

	def test_rotate_secret(self):
		settings = configure_settings()
		before = settings.get_password("ingest_secret")
		settings.rotate_secret("ingest_secret")
		after = frappe.get_doc("Ops Alert Settings").get_password("ingest_secret")
		self.assertNotEqual(before, after)
		self.assertRaises(frappe.ValidationError, setup.rotate_secret, "heartbeat_url")


class TestOpsAlertSettingsUnderLoad(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()

	def test_form_stays_saveable_while_the_jobs_run(self):
		"""The minute and five-minute jobs record their run times on this Single; an operator
		editing the form for longer than that must still be able to save."""
		from unittest.mock import patch

		from frappe.desk.form.save import savedocs

		from hostel360.hostel_operations import escalation, health

		configure_settings()
		form = frappe.get_doc("Ops Alert Settings").as_dict()

		moment = add_to_date(now_datetime(), days=3650, minutes=int(frappe.generate_hash(length=6), 16) % 500_000)
		with patch.object(escalation, "escalate_open_alerts", return_value={}):
			self.assertEqual(escalation.run_escalations(now=moment), {})
		with patch.object(health, "CHECKS", ()), DeliveryRecorder():
			health._run_and_apply()

		form["critical_renotify_minutes"] = 45
		savedocs(json.dumps(form, default=str), "Save")

		self.assertEqual(frappe.db.get_single_value("Ops Alert Settings", "critical_renotify_minutes"), 45)
		# The stale run time the form was opened with is not written back over the job's.
		self.assertEqual(
			get_datetime(frappe.db.get_single_value("Ops Alert Settings", "last_escalation_run_on")), moment
		)

	def test_level_without_contacts_is_flagged_but_saveable(self):
		from hostel360.hostel_operations import health
		from hostel360.hostel_operations.settings import get_settings

		settings = configure_settings()
		settings.escalation_levels[1].notify_emails = ""
		settings.escalation_levels[1].notify_mobiles = ""
		frappe.local.message_log = []
		settings.save()
		clear_settings_cache()
		self.assertTrue(
			any("no e-mail address or mobile number" in str(message) for message in frappe.local.message_log)
		)

		def check():
			return health.check_escalation_contacts(
				frappe._dict(settings=get_settings(), now=now_datetime(), read_only=True)
			)

		result = check()
		self.assertEqual(result.status, health.WARN)
		self.assertIn("level 2", result.message)

		configure_settings()
		self.assertEqual(check().status, health.OK)


def _plain_user() -> str:
	email = "ops-settings-plain@example.com"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Plain", "send_welcome_email": 0, "user_type": "System User"}
		).insert(ignore_permissions=True)
	return email
