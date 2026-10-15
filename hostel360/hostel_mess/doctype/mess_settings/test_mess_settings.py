# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_mess.setup import STARTER_ITEMS, install_defaults
from hostel360.hostel_mess.testing import EXTERNAL_DEPENDENCIES, set_mess_settings
from hostel360.hostel_mess.utils import DEFAULT_SETTINGS, get_mess_settings

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


class TestMessSettings(IntegrationTestCase):
	def test_install_defaults_is_idempotent_and_keeps_choices(self):
		set_mess_settings(reduction_min_days=5, max_reduction_days_per_month=0)
		frappe.db.delete("Singles", {"doctype": "Mess Settings", "field": "bill_due_days"})
		items_before = frappe.db.count("Mess Item")

		install_defaults()
		install_defaults()

		settings = get_mess_settings()
		self.assertEqual(settings.reduction_min_days, 5)
		self.assertEqual(settings.max_reduction_days_per_month, 0)
		self.assertEqual(settings.bill_due_days, DEFAULT_SETTINGS["bill_due_days"])
		self.assertEqual(frappe.db.count("Mess Item"), items_before)

	def test_starter_items_only_on_empty_install(self):
		self.assertTrue(frappe.db.count("Mess Item"))
		names = {row[0] for row in STARTER_ITEMS}
		self.assertTrue({"Toor Dal", "Idli Rice", "LPG Cylinder (Commercial 19 kg)"} <= names)
		self.assertEqual(len(names), len(STARTER_ITEMS))
		self.assertEqual(len({row[2] for row in STARTER_ITEMS}), len(STARTER_ITEMS))

	def test_unsaved_settings_fall_back_to_defaults(self):
		frappe.db.delete("Singles", {"doctype": "Mess Settings", "field": "reduction_approver"})
		self.assertEqual(get_mess_settings().reduction_approver, "Deputy Warden")

	def test_validation(self):
		settings = frappe.get_doc("Mess Settings")
		settings.reduction_min_days = 4
		settings.max_reduction_days_per_month = 3
		with self.assertRaisesRegex(frappe.ValidationError, "monthly limit"):
			settings.save()
		settings.max_reduction_days_per_month = 0
		settings.reduction_min_days = 0
		with self.assertRaises(frappe.ValidationError):
			settings.save()
