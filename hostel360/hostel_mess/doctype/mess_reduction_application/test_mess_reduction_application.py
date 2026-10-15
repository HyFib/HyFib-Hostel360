# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_months, get_first_day, nowdate

from hostel360.hostel_mess.services import (
	apply_mess_reduction,
	cancel_mess_reduction,
	decide_mess_reduction,
	list_mess_reductions,
)
from hostel360.hostel_mess.testing import (
	EXTERNAL_DEPENDENCIES,
	make_hostel,
	make_mess,
	make_reduction,
	make_student,
	make_user,
	set_mess_settings,
)
from hostel360.setup.roles import DEPUTY_WARDEN_ROLE, MESS_MANAGER_ROLE, STUDENT_ROLE

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES

DOCTYPE_MODULE = "hostel360.hostel_mess.doctype.mess_reduction_application.mess_reduction_application"


def in_days(n):
	return add_days(nowdate(), n)


class TestMessReductionApplication(IntegrationTestCase):
	def setUp(self):
		set_mess_settings(
			reduction_min_days=3,
			reduction_advance_notice_days=1,
			reduction_approver="Deputy Warden",
			max_reduction_days_per_month=0,
		)
		self.hostel = make_hostel()
		self.mess = make_mess([self.hostel])
		self.student_user = make_user([STUDENT_ROLE], user_type="Website User")
		self.student = make_student(self.hostel, user=self.student_user)
		self.deputy = make_user([DEPUTY_WARDEN_ROLE])
		self.mess_manager = make_user([MESS_MANAGER_ROLE])

		notify = patch(f"{DOCTYPE_MODULE}.send_notification")
		self.notify_submitted = notify.start()
		self.addCleanup(notify.stop)
		decided = patch("hostel360.hostel_mess.services.send_notification")
		self.notify_decided = decided.start()
		self.addCleanup(decided.stop)

	def apply(self, start, end, user=None, reason="Home Visit", student=None):
		return apply_mess_reduction(
			student or self.student,
			start,
			end,
			reason,
			description="Going home",
			user=user or self.student_user,
		)

	def test_application_sets_derived_values_and_alerts_approvers(self):
		name = self.apply(in_days(2), in_days(5))
		doc = frappe.get_doc("Mess Reduction Application", name)
		self.assertEqual(doc.status, "Pending")
		self.assertEqual(doc.number_of_days, 4)
		self.assertEqual(doc.mess, self.mess)
		self.assertEqual(doc.hostel, self.hostel)
		self.assertEqual(doc.applied_by, self.student_user)
		self.assertEqual(self.notify_submitted.call_args.args[0], "mess_reduction_submitted")
		context = self.notify_submitted.call_args.args[2]
		self.assertEqual(context["application"], name)
		self.assertEqual(context["number_of_days"], 4)

	def test_minimum_days(self):
		with self.assertRaisesRegex(frappe.ValidationError, "at least 3 days"):
			self.apply(in_days(2), in_days(3))

	def test_advance_notice(self):
		with self.assertRaisesRegex(frappe.ValidationError, "in advance"):
			self.apply(nowdate(), in_days(3))
		# Approvers may record an absence that has already started.
		self.assertTrue(self.apply(in_days(-1), in_days(3), user=self.deputy))

	def test_overlap(self):
		first = self.apply(in_days(2), in_days(5))
		with self.assertRaisesRegex(frappe.ValidationError, "overlap"):
			self.apply(in_days(4), in_days(8))
		cancel_mess_reduction(first, self.student_user)
		self.assertTrue(self.apply(in_days(4), in_days(8)))

	def test_monthly_cap(self):
		set_mess_settings(max_reduction_days_per_month=5)
		next_month = get_first_day(add_months(nowdate(), 1))
		self.apply(add_days(next_month, 1), add_days(next_month, 3))
		with self.assertRaisesRegex(frappe.ValidationError, "At most 5"):
			self.apply(add_days(next_month, 10), add_days(next_month, 12))
		# The cap is per calendar month; the month after is free.
		month_after = get_first_day(add_months(nowdate(), 2))
		self.assertTrue(self.apply(add_days(month_after, 1), add_days(month_after, 3)))

	def test_only_enrolled_active_residents(self):
		outsider = make_student(self.hostel, mess_enrolled=0)
		with self.assertRaisesRegex(frappe.ValidationError, "not enrolled"):
			self.apply(in_days(2), in_days(5), user="Administrator", student=outsider)

	def test_portal_user_cannot_apply_for_someone_else(self):
		other_user = make_user([STUDENT_ROLE], user_type="Website User")
		make_student(self.hostel, user=other_user)
		with self.assertRaises(frappe.PermissionError):
			self.apply(in_days(2), in_days(5), user=other_user)

	def test_decision_roles(self):
		name = self.apply(in_days(2), in_days(5))
		with self.assertRaises(frappe.PermissionError):
			decide_mess_reduction(name, True, None, self.mess_manager)
		with self.assertRaisesRegex(frappe.ValidationError, "reason"):
			decide_mess_reduction(name, False, " ", self.deputy)

		with patch(
			"hostel360.hostel_mess.services.student_recipients", return_value=[{"user": self.student_user}]
		) as rcpt:
			decide_mess_reduction(name, True, "Enjoy the break", self.deputy)
		rcpt.assert_called_once_with(self.student, include_student=True, include_guardians=True)

		doc = frappe.get_doc("Mess Reduction Application", name)
		self.assertEqual(
			(doc.status, doc.approver, doc.approval_remarks), ("Approved", self.deputy, "Enjoy the break")
		)
		self.assertTrue(doc.decided_on)
		event, recipients, context = self.notify_decided.call_args.args[:3]
		self.assertEqual(event, "mess_reduction_decided")
		self.assertEqual(recipients, [{"user": self.student_user}])
		self.assertEqual(context["status"], "Approved")

		with self.assertRaisesRegex(frappe.ValidationError, "already approved"):
			decide_mess_reduction(name, False, "Changed mind", self.deputy)

	def test_configured_approver_role(self):
		set_mess_settings(reduction_approver="Mess Manager")
		name = self.apply(in_days(2), in_days(5))
		decide_mess_reduction(name, "reject", "Exams that week", self.mess_manager)
		self.assertEqual(frappe.db.get_value("Mess Reduction Application", name, "status"), "Rejected")

		second = self.apply(in_days(10), in_days(13))
		with self.assertRaises(frappe.PermissionError):
			decide_mess_reduction(second, True, None, self.deputy)

	def test_cancellation_rules(self):
		pending = self.apply(in_days(2), in_days(5))
		cancel_mess_reduction(pending, self.student_user)
		self.assertEqual(frappe.db.get_value("Mess Reduction Application", pending, "status"), "Cancelled")
		with self.assertRaises(frappe.ValidationError):
			cancel_mess_reduction(pending, self.student_user)

		started = make_reduction(self.student, in_days(-1), in_days(3))
		with self.assertRaisesRegex(frappe.ValidationError, "already started"):
			cancel_mess_reduction(started, self.student_user)
		cancel_mess_reduction(started, self.deputy)
		self.assertEqual(frappe.db.get_value("Mess Reduction Application", started, "status"), "Cancelled")

	def test_status_changes_only_through_actions(self):
		doc = frappe.get_doc("Mess Reduction Application", self.apply(in_days(2), in_days(5)))
		doc.status = "Approved"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_list_for_portal(self):
		name = self.apply(in_days(2), in_days(5))
		rows = list_mess_reductions(self.student)
		self.assertEqual(rows[0]["name"], name)
		self.assertTrue(rows[0]["can_cancel"])
		self.assertEqual(rows[0]["number_of_days"], 4)
