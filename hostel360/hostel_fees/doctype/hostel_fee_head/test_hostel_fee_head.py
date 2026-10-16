# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe

from hostel360.hostel_fees.setup import DEFAULT_FEE_HEADS, install_defaults
from hostel360.hostel_fees.tests import fixtures as fee_fixtures
from hostel360.hostel_fees.tests.fixtures import FeeTestCase

# Read by the Frappe test runner: fee tests build every linked record they need themselves.
IGNORE_TEST_RECORD_DEPENDENCIES = fee_fixtures.IGNORE_TEST_RECORD_DEPENDENCIES


class TestHostelFeeHead(FeeTestCase):
	def test_install_defaults_is_idempotent(self):
		install_defaults()
		count = frappe.db.count("Hostel Fee Head")
		install_defaults()
		self.assertEqual(frappe.db.count("Hostel Fee Head"), count)

		for name, category, refundable, _description in DEFAULT_FEE_HEADS:
			head = frappe.db.get_value(
				"Hostel Fee Head", name, ["fee_category", "is_refundable"], as_dict=True
			)
			self.assertIsNotNone(head, name)
			self.assertEqual(head.fee_category, category)
			self.assertEqual(head.is_refundable, refundable)

		self.assertEqual(frappe.db.get_value("Hostel Fee Head", "Caution Deposit", "is_refundable"), 1)

	def test_install_defaults_keeps_administrator_settings(self):
		frappe.db.set_single_value("Hostel Payment Settings", "transaction_expiry_minutes", 90)
		install_defaults()
		self.assertEqual(
			frappe.db.get_single_value("Hostel Payment Settings", "transaction_expiry_minutes"), 90
		)

	def test_heads_used_by_other_modules_are_protected(self):
		install_defaults()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Hostel Fee Head", "Mess Charges")

		head = frappe.get_doc("Hostel Fee Head", "Mess Extras")
		head.is_active = 0
		with self.assertRaises(frappe.ValidationError):
			head.save()

	def test_other_heads_can_be_deactivated(self):
		install_defaults()
		head = frappe.get_doc("Hostel Fee Head", "Electricity Charges")
		head.is_active = 0
		head.save()
		self.assertEqual(frappe.db.get_value("Hostel Fee Head", "Electricity Charges", "is_active"), 0)
