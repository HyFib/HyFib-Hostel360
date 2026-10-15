# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from hostel360.student_services.number_card.students_on_leave_now.students_on_leave_now import (
	get_students_on_leave,
	is_away,
)


def leave(**values):
	now = now_datetime()
	row = {
		"student": "STUDENT-1",
		"status": "Approved",
		"from_datetime": add_to_date(now, hours=-5),
		"to_datetime": add_to_date(now, hours=5),
		"checked_out_on": None,
		"returned_on": None,
	}
	row.update(values)
	return frappe._dict(row)


class TestStudentsOnLeaveNow(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_approved_leave_counts_while_its_period_runs(self):
		now = now_datetime()
		self.assertTrue(is_away(leave(), now))
		self.assertFalse(is_away(leave(to_datetime=add_to_date(now, hours=-1)), now))

	def test_recorded_check_out_decides(self):
		now = now_datetime()
		self.assertTrue(is_away(leave(status="Checked Out", checked_out_on=add_to_date(now, hours=-2)), now))
		# a check-out recorded for later in the day has not happened yet
		self.assertFalse(is_away(leave(checked_out_on=add_to_date(now, hours=2)), now))

	def test_recorded_return_ends_the_leave(self):
		now = now_datetime()
		back = leave(status="Returned", checked_out_on=add_to_date(now, hours=-4), returned_on=add_to_date(now, hours=-1))
		self.assertFalse(is_away(back, now))
		later = leave(status="Returned", returned_on=add_to_date(now, hours=3))
		self.assertTrue(is_away(later, now))

	def test_overdue_leave_stays_away_until_return(self):
		now = now_datetime()
		overdue = leave(status="Overdue", from_datetime=add_to_date(now, days=-3), to_datetime=add_to_date(now, days=-1))
		self.assertTrue(is_away(overdue, now))

	def test_card_agrees_with_students_on_leave_report(self):
		from hostel360.student_services.report.students_on_leave.students_on_leave import execute

		_columns, rows, *_rest = execute({})
		card = get_students_on_leave()
		self.assertEqual(card["value"], len({row["student"] for row in rows}))
		self.assertEqual(card["route"], ["query-report", "Students On Leave"])

	def test_guest_is_refused(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			get_students_on_leave()
