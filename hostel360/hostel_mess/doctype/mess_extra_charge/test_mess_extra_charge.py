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

	def vacate(self):
		"""What Room Allotment does when the student leaves: the current hostel is cleared."""
		values = {"current_hostel": None, "status": "Vacated"}
		if frappe.get_meta("Hostel Student").has_field("last_hostel"):
			values["last_hostel"] = self.hostel
		frappe.db.set_value("Hostel Student", self.student, values)

	def test_hostel_is_kept_after_the_student_moves_out(self):
		charge = self.make_charge()
		self.vacate()
		charge.reload()
		charge.amount = 75
		charge.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Mess Extra Charge", charge.name, "hostel"), self.hostel)

	def test_charge_for_a_vacated_student_uses_their_last_hostel(self):
		if not frappe.get_meta("Hostel Student").has_field("last_hostel"):
			self.skipTest("Hostel Student has no last_hostel field on this site")
		self.vacate()
		charge = self.make_charge()
		self.assertEqual((charge.hostel, charge.mess), (self.hostel, self.mess))

	def test_migration_restores_a_lost_hostel(self):
		from hostel360.hostel_mess.setup import restore_student_hostels

		charge = self.make_charge()
		frappe.db.set_value("Mess Extra Charge", charge.name, "hostel", None)  # as older saves left it
		self.vacate()
		restore_student_hostels()
		expected = self.hostel if frappe.get_meta("Hostel Student").has_field("last_hostel") else None
		self.assertEqual(frappe.db.get_value("Mess Extra Charge", charge.name, "hostel"), expected)
