# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_setup.setup import install_defaults


class TestHostelSettings(IntegrationTestCase):
	def test_install_defaults_is_idempotent(self):
		install_defaults()
		settings = frappe.get_single("Hostel Settings")
		self.assertEqual(settings.institution_short_name, "ACGCET")
		self.assertEqual(settings.support_email, "support@hyfib.com")
		self.assertEqual(settings.support_phone, "+91 96060 60596")
		self.assertTrue(settings.privacy_notice_version)
		self.assertIn("Digital Personal Data Protection Act, 2023", settings.privacy_notice)
		self.assertTrue(settings.current_academic_year)

		facilities = frappe.db.count("Room Facility")
		years = frappe.db.count("Hostel Academic Year")

		# An administrator's edits survive a later deploy.
		frappe.db.set_single_value("Hostel Settings", "support_phone", "+91 90000 00000")
		install_defaults()
		install_defaults()
		self.assertEqual(frappe.db.get_single_value("Hostel Settings", "support_phone"), "+91 90000 00000")
		self.assertEqual(frappe.db.count("Room Facility"), facilities)
		self.assertEqual(frappe.db.count("Hostel Academic Year"), years)
		self.assertEqual(frappe.db.count("Hostel Academic Year", {"is_current": 1}), 1)

	def test_urgent_escalation_must_be_shorter(self):
		settings = frappe.get_single("Hostel Settings")
		settings.grievance_escalation_hours = 4
		settings.grievance_urgent_escalation_hours = 10
		self.assertRaises(frappe.ValidationError, settings.save)

	def test_portal_url_is_normalised(self):
		settings = frappe.get_single("Hostel Settings")
		settings.portal_url = "https://hostel.example.ac.in/"
		settings.save()
		self.assertEqual(settings.portal_url, "https://hostel.example.ac.in")

		settings.portal_url = "hostel.example.ac.in"
		self.assertRaises(frappe.ValidationError, settings.save)
