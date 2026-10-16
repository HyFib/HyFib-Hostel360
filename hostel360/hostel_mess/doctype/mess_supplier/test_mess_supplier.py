# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_mess.testing import EXTERNAL_DEPENDENCIES, unique

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


class TestMessSupplier(IntegrationTestCase):
	def make_supplier(self, **values):
		return frappe.get_doc(
			{"doctype": "Mess Supplier", "supplier_name": unique("Supplier"), **values}
		).insert(ignore_permissions=True)

	def test_mobile_and_gstin_are_normalised(self):
		supplier = self.make_supplier(mobile_number="+91 98765 43210", gstin="33aaacb1234c1z5")
		self.assertEqual(supplier.mobile_number, "9876543210")
		self.assertEqual(supplier.gstin, "33AAACB1234C1Z5")

	def test_invalid_values_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_supplier(mobile_number="12345")
		with self.assertRaises(frappe.ValidationError):
			self.make_supplier(gstin="33ABC")
