# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime, nowdate

from hostel360.setup.roles import DEPUTY_WARDEN_ROLE, OFFICE_ROLE, WARDEN_ROLE
from hostel360.student_services import services
from hostel360.student_services.report.bonafide_register.bonafide_register import execute as bonafide_register
from hostel360.student_services.report.grievance_summary.grievance_summary import execute as grievance_summary
from hostel360.student_services.report.leave_register.leave_register import execute as leave_register
from hostel360.student_services.report.students_on_leave.students_on_leave import execute as students_on_leave
from hostel360.student_services.testing import (
	leave_data,
	make_hostel,
	make_student,
	make_user,
	scope_to_hostel,
	set_hostel_settings,
)


class TestStudentServicesReports(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_hostel_settings(leave_requires_parent_approval=0, leave_return_grace_hours=2)
		self.hostel = make_hostel()
		self.warden = make_user([WARDEN_ROLE])
		self.office = make_user([OFFICE_ROLE])

	def tearDown(self):
		frappe.set_user("Administrator")

	def away_leave(self, hours_ago=10, back_in_hours=20, check_out=True):
		student = make_student(self.hostel)
		start = add_to_date(now_datetime(), hours=-hours_ago)
		data = {**leave_data(), "from_datetime": start, "to_datetime": add_to_date(now_datetime(), hours=back_in_hours)}
		name = services.apply_leave(student.name, data, "Warden", self.office)
		services.warden_decision(name, True, None, self.warden)
		if check_out:
			frappe.get_doc("Home Visit Leave", name).record_check_out(self.warden, start)
		return name

	def test_students_on_leave_as_of_a_moment(self):
		away = self.away_leave()
		not_checked_out = self.away_leave(check_out=False)
		returned = self.away_leave()
		frappe.get_doc("Home Visit Leave", returned).record_return(self.warden, add_to_date(now_datetime(), hours=-1))
		overdue = self.away_leave(hours_ago=40, back_in_hours=-5)

		columns, rows, _message, _chart, summary = students_on_leave({"hostel": self.hostel})
		by_leave = {row["name"]: row for row in rows}
		self.assertIn(away, by_leave)
		self.assertIn(not_checked_out, by_leave)
		self.assertNotIn(returned, by_leave)
		self.assertGreater(by_leave[overdue]["overdue_hours"], 4)
		self.assertIsNone(by_leave[away]["overdue_hours"])
		self.assertEqual(summary[0]["value"], 3)

		# Two hours ago the returned student was still away
		earlier = add_to_date(now_datetime(), hours=-2)
		rows = students_on_leave({"hostel": self.hostel, "as_of": str(earlier)})[1]
		self.assertIn(returned, {row["name"] for row in rows})

	def test_leave_register(self):
		late = self.away_leave(hours_ago=40, back_in_hours=-5)
		frappe.get_doc("Home Visit Leave", late).record_return(self.warden)
		columns, rows, _message, chart, summary = leave_register(
			{"from_date": add_to_date(nowdate(), days=-3), "to_date": nowdate(), "hostel": self.hostel}
		)
		row = next(r for r in rows if r.name == late)
		self.assertGreaterEqual(row.late_by_hours, 4.9)
		self.assertEqual(chart["type"], "bar")
		self.assertEqual(summary[0]["value"], len(rows))

	def test_grievance_summary_respects_confidentiality(self):
		student = make_student(self.hostel)
		base = {"subject": "Problem", "description": "Details", "priority": "Medium"}
		services.raise_grievance(student.name, {**base, "category": "Electrical"}, student.user)
		services.raise_grievance(student.name, {**base, "category": "Ragging or Harassment"}, student.user)
		filters = {"from_date": nowdate(), "to_date": nowdate(), "hostel": self.hostel}

		frappe.set_user(self.warden)
		columns, rows, _message, chart, summary = grievance_summary(filters)
		self.assertEqual({row["group"] for row in rows}, {"Electrical", "Ragging or Harassment"})
		self.assertEqual(summary[0]["value"], 2)
		self.assertTrue(chart["data"]["datasets"])

		deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(deputy, self.hostel)
		frappe.set_user(deputy)
		rows = grievance_summary(filters)[1]
		self.assertEqual({row["group"] for row in rows}, {"Electrical"})

	def test_bonafide_register(self):
		student = make_student(self.hostel)
		name = services.request_bonafide(student.name, "Scholarship", "BC/MBC scholarship")
		frappe.get_doc("Bonafide Certificate Request", name).approve_by(self.warden)
		columns, rows, _message, _chart, summary = bonafide_register({"hostel": self.hostel})
		row = next(r for r in rows if r.name == name)
		self.assertEqual(row.validity, "Valid")
		self.assertTrue(row.certificate_number.startswith("BC/"))
		self.assertEqual(summary[2]["value"], 1)
