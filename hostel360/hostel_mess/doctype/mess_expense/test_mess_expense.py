# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from hostel360.hostel_mess.billing import get_other_expenses
from hostel360.hostel_mess.testing import EXTERNAL_DEPENDENCIES, make_expense, make_hostel, make_mess

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


class TestMessExpense(IntegrationTestCase):
	def test_only_submitted_expenses_count(self):
		mess = make_mess([make_hostel()])
		day = add_days(nowdate(), -3)
		make_expense(mess, 1200, day, "Gas")
		make_expense(mess, 800, day, "Wages", submit=False)
		cancelled = make_expense(mess, 300, day, "Water")
		cancelled.cancel()
		self.assertEqual(get_other_expenses(mess, day, nowdate()), 1200)

	def test_validation(self):
		mess = make_mess([make_hostel()])
		with self.assertRaises(frappe.ValidationError):
			make_expense(mess, 0, nowdate())
		with self.assertRaises(frappe.ValidationError):
			make_expense(mess, 100, add_days(nowdate(), 2))
