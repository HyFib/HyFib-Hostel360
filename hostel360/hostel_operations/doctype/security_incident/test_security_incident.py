# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from hostel360.hostel_operations import alerts, health
from hostel360.hostel_operations.doctype.security_incident.security_incident import (
	declare_from_alert,
	reporting_alert_key,
)
from hostel360.hostel_operations.tests.helpers import (
	DeliveryRecorder,
	clear_settings_cache,
	configure_settings,
	unique_key,
)

MALWARE = "Malicious code (virus, worm, trojan, bot, spyware, ransomware, cryptominer)"


class TestSecurityIncident(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()

	def setUp(self):
		configure_settings()
		self.recorder = DeliveryRecorder().__enter__()
		self.addCleanup(self.recorder.__exit__, None, None, None)

	def make(self, hours_ago: float = 1, **values):
		doc = frappe.get_doc(
			{
				"doctype": "Security Incident",
				"title": "Web shell found in an uploaded file",
				"category": MALWARE,
				"severity": "Critical",
				"description": "<p>maldet quarantined /home/frappe/sites/private/files/x.php</p>",
				"detected_on": add_to_date(now_datetime(), hours=-hours_ago),
				**values,
			}
		)
		return doc.insert()

	def test_deadline_is_six_hours_after_detection(self):
		doc = self.make(hours_ago=1)
		self.assertEqual(get_datetime(doc.cert_in_deadline), add_to_date(get_datetime(doc.detected_on), hours=6))
		self.assertEqual(doc.status, "Open")
		self.assertFalse(doc.reporting_sla_breached)

	def test_status_follows_the_reports(self):
		doc = self.make(hours_ago=2)
		doc.reported_to_certin_on = add_to_date(now_datetime(), hours=-1)
		doc.cert_in_reference = "CERTIn-2026-0001"
		doc.save()
		self.assertEqual(doc.status, "Open", "the institution has not been told yet")
		self.assertEqual(doc.reported_by, frappe.session.user)

		doc.reported_to_institution_on = now_datetime()
		doc.save()
		self.assertEqual(doc.status, "Reported")
		self.assertFalse(doc.reporting_sla_breached)

		doc.reported_to_institution_on = None
		doc.save()
		self.assertEqual(doc.status, "Open")

	def test_late_report_is_marked_as_a_missed_deadline(self):
		doc = self.make(hours_ago=8)
		self.assertTrue(doc.reporting_sla_breached)
		doc.reported_to_certin_on = add_to_date(now_datetime(), hours=-1)
		doc.reported_to_institution_on = add_to_date(now_datetime(), hours=-1)
		doc.save()
		self.assertEqual(doc.status, "Reported")
		self.assertTrue(doc.reporting_sla_breached, "reported after the six hours")

	def test_closing_needs_both_reports_and_an_assessment(self):
		doc = self.make()
		doc.status = "Closed"
		self.assertRaises(frappe.ValidationError, doc.save)

		doc.reload()
		doc.reported_to_certin_on = now_datetime()
		doc.reported_to_institution_on = now_datetime()
		doc.status = "Closed"
		self.assertRaises(frappe.ValidationError, doc.save)

		doc.reload()
		doc.reported_to_certin_on = now_datetime()
		doc.reported_to_institution_on = now_datetime()
		doc.status = "Closed"
		doc.assessment_notes = "Upload scanning blocked the file; no other file touched."
		doc.save()
		self.assertEqual(doc.status, "Closed")
		self.assertTrue(doc.closed_on)

	def test_not_reportable_needs_a_reason(self):
		doc = self.make()
		doc.cert_in_reportable = 0
		self.assertRaises(frappe.ValidationError, doc.save)
		doc.reload()
		doc.cert_in_reportable = 0
		doc.assessment_notes = "EICAR test file uploaded by HyFib during the scanner check."
		doc.save()
		self.assertEqual(doc.status, "Not Reportable")
		self.assertFalse(doc.reporting_sla_breached)

	def test_report_times_must_be_plausible(self):
		doc = self.make(hours_ago=2)
		doc.reported_to_certin_on = add_to_date(now_datetime(), hours=-3)
		self.assertRaises(frappe.ValidationError, doc.save)
		doc.reload()
		doc.reported_to_certin_on = add_to_date(now_datetime(), hours=2)
		self.assertRaises(frappe.ValidationError, doc.save)
		self.assertRaises(frappe.ValidationError, self.make, hours_ago=-1)

	def test_deadline_alerts_warn_at_four_hours_and_page_at_six(self):
		doc = self.make(hours_ago=3)
		key = reporting_alert_key(doc.name)

		with patch.object(health, "CHECKS", (health.check_security_incidents,)):
			health._run_and_apply()
			self.assertFalse(frappe.db.exists("Ops Alert", {"open_key": key}), "three hours in: nothing yet")

			health._run_and_apply(now=add_to_date(now_datetime(), hours=1, minutes=30))
			alert = frappe.db.get_value("Ops Alert", {"open_key": key}, ["name", "severity", "category"], as_dict=True)
			self.assertEqual((alert.severity, alert.category), ("Warning", "Security"))

			health._run_and_apply(now=add_to_date(now_datetime(), hours=3, minutes=30))
			self.assertEqual(frappe.db.get_value("Ops Alert", alert.name, "severity"), "Critical")
			self.assertIn("deadline missed", frappe.db.get_value("Ops Alert", alert.name, "title"))
			self.assertTrue(frappe.db.get_value("Security Incident", doc.name, "reporting_sla_breached"))
		self.assertEqual(self.recorder.events(alert.name), ["raised", "severity"])

		doc.reload()
		doc.reported_to_certin_on = now_datetime()
		doc.reported_to_institution_on = now_datetime()
		doc.save()
		self.assertEqual(frappe.db.get_value("Ops Alert", alert.name, "status"), "Resolved")

	def test_declare_from_alert(self):
		name = alerts.raise_alert(
			unique_key("malware-detected"), "Malware detected in an uploaded file", "Critical", "Security", source="Security"
		)
		incident = declare_from_alert(name)
		doc = frappe.get_doc("Security Incident", incident)
		self.assertEqual(doc.ops_alert, name)
		self.assertEqual(doc.category, MALWARE)
		self.assertEqual(doc.severity, "Critical")
		self.assertEqual(get_datetime(doc.detected_on), get_datetime(frappe.db.get_value("Ops Alert", name, "first_seen")))
		self.assertEqual(declare_from_alert(name), incident, "one incident per alert")
		self.assertTrue(
			frappe.db.exists(
				"Comment", {"reference_doctype": "Ops Alert", "reference_name": name, "content": ("like", f"%{incident}%")}
			)
		)

	def test_only_operations_can_declare(self):
		name = alerts.raise_alert(unique_key("security"), "Suspicious logins", "Warning", "Security")
		email = "incident-plain@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Plain", "send_welcome_email": 0, "user_type": "System User"}
			).insert(ignore_permissions=True)
		with self.set_user(email):
			self.assertRaises(frappe.PermissionError, declare_from_alert, name)
