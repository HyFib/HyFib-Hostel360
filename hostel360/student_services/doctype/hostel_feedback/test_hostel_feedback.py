# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_months, getdate, nowdate

from hostel360.student_services import services
from hostel360.student_services.doctype.hostel_feedback.hostel_feedback import current_period, has_submitted
from hostel360.student_services.report.feedback_summary.feedback_summary import execute as feedback_summary
from hostel360.student_services.testing import make_hostel, make_student

IGNORE_TEST_RECORD_DEPENDENCIES = ["Hostel Student", "Hostel", "User"]


class TestHostelFeedback(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.hostel = make_hostel()
		self.student = make_student(self.hostel)

	def submit(self, student=None, **data):
		values = {"feedback_type": "Mess", "overall": 4, "food_quality": 3, "comments": "Sambar too watery", **data}
		return services.submit_feedback((student or self.student).name, values)

	def test_stars_are_stored_as_rating_fractions(self):
		doc = frappe.get_doc("Hostel Feedback", self.submit())
		self.assertEqual(doc.overall, 0.8)
		self.assertEqual(doc.food_quality, 0.6)
		self.assertEqual(doc.period, current_period())
		self.assertEqual(doc.hostel, self.hostel)
		self.assertEqual(doc.student, self.student.name)
		self.assertEqual(doc.student_name, self.student.full_name)

	def test_one_feedback_per_type_per_month(self):
		self.submit()
		with self.assertRaises(frappe.DuplicateEntryError):
			self.submit(overall=2)
		self.submit(feedback_type="Hostel")
		last_month = getdate(add_months(nowdate(), -1)).strftime("%Y-%m")
		self.submit(period=last_month)
		self.assertTrue(has_submitted(self.student.name, "Mess", current_period()))
		self.assertFalse(has_submitted(self.student.name, "General", current_period()))

	def test_anonymous_feedback_does_not_record_the_student(self):
		doc = frappe.get_doc("Hostel Feedback", self.submit(is_anonymous=1))
		self.assertIsNone(doc.student)
		self.assertIsNone(doc.student_name)
		self.assertEqual(doc.hostel, self.hostel)
		self.assertNotIn(self.student.name, doc.submission_key)
		# ... yet the same student cannot submit twice for the month
		with self.assertRaises(frappe.DuplicateEntryError):
			self.submit()

	def test_period_and_ratings_are_validated(self):
		next_month = getdate(add_months(nowdate(), 1)).strftime("%Y-%m")
		for data in ({"period": next_month}, {"period": "2026-13"}, {"period": "Oct 2026"}, {"overall": 0}):
			with self.assertRaises(frappe.ValidationError):
				self.submit(**data)
		with self.assertRaises(frappe.ValidationError):
			self.submit(overall=7)

	def test_ineligible_students_cannot_give_feedback(self):
		suspended = make_student(self.hostel, status="Suspended")
		with self.assertRaises(frappe.ValidationError):
			self.submit(student=suspended)

	def test_student_answers_are_locked_but_staff_can_note_action(self):
		doc = frappe.get_doc("Hostel Feedback", self.submit())
		doc.action_taken = "Spoke to the cook about the sambar."
		doc.save(ignore_permissions=True)
		doc.overall = 0.2
		with self.assertRaises(frappe.CannotChangeConstantError):
			doc.save(ignore_permissions=True)

	def test_summary_report_hides_anonymous_students(self):
		named = self.submit(overall=5, comments="Great food this month")
		other = make_student(self.hostel, first_name="Priya", gender="Female")
		anonymous = self.submit(student=other, overall=2, comments="Rice was undercooked", is_anonymous=1)
		filters = {"from_period": current_period(), "to_period": current_period(), "hostel": self.hostel}

		columns, rows, *_rest = feedback_summary(filters)
		mess = next(row for row in rows if row["feedback_type"] == "Mess")
		self.assertEqual(mess["responses"], 2)
		self.assertEqual(mess["anonymous"], 1)
		self.assertEqual(mess["avg_overall"], 3.5)

		columns, rows, *_rest = feedback_summary({**filters, "show_comments": 1})
		by_name = {row["name"]: row for row in rows}
		self.assertEqual(by_name[anonymous]["student_name"], "Anonymous")
		self.assertEqual(by_name[named]["student_name"], self.student.full_name)
		self.assertNotIn("student", {column["fieldname"] for column in columns})
