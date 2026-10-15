# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from hostel360.hostel_mess.testing import EXTERNAL_DEPENDENCIES, make_hostel, make_mess, make_student

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


class TestMessExtraCharge(IntegrationTestCase):
	def setUp(self):
		self.hostel = make_hostel()
		self.mess = make_mess([self.hostel])
		self.student = make_student(self.hostel)

	def make_charge(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Mess Extra Charge",
				"student": self.student,
				"charge_date": nowdate(),
				"description": "Guest lunch",
				"amount": 60,
				**values,
			}
		).insert(ignore_permissions=True)

	def test_defaults_from_student(self):
		charge = self.make_charge()
		self.assertEqual((charge.mess, charge.hostel, charge.status), (self.mess, self.hostel, "Unbilled"))

	def test_validation(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_charge(amount=0)
		with self.assertRaises(frappe.ValidationError):
			self.make_charge(charge_date=add_days(nowdate(), 1))

	def test_billed_charge_is_locked(self):
		charge = self.make_charge()
		frappe.db.set_value("Mess Extra Charge", charge.name, "status", "Billed")
		charge.reload()
		charge.amount = 10
		with self.assertRaises(frappe.ValidationError):
			charge.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			charge.delete(ignore_permissions=True)
