# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.client import insert as client_insert
from frappe.client import set_value as client_set_value
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime, nowdate

from hostel360.setup.roles import (
	AUDITOR_ROLE,
	DEPUTY_WARDEN_ROLE,
	MESS_MANAGER_ROLE,
	OFFICE_ROLE,
	SECURITY_ROLE,
	WARDEN_ROLE,
)
from hostel360.student_services import services
from hostel360.student_services.leave_tasks import mark_overdue_returns
from hostel360.student_services.permissions import hostel_scoped_has_permission
from hostel360.student_services.testing import (
	capture_notifications,
	leave_data,
	make_block_deputy,
	make_block_room,
	make_guardian,
	make_hostel,
	make_linked_reduction,
	make_student,
	make_user,
	put_in_room,
	run_form_action,
	scope_to_hostel,
	set_hostel_settings,
)
from hostel360.student_services.utils import people_service

IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Hostel Student",
	"Hostel Guardian",
	"Hostel",
	"Hostel Room",
	"Mess Reduction Application",
	"User",
]


class TestHomeVisitLeave(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_hostel_settings(leave_requires_parent_approval=1, leave_return_grace_hours=2)
		self.hostel = make_hostel()
		self.student = make_student(self.hostel)
		self.guardian = make_guardian(self.student.name)
		self.deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(self.deputy, self.hostel)
		self.warden = make_user([WARDEN_ROLE])

	def tearDown(self):
		frappe.set_user("Administrator")

	def apply(self, applied_by="Student", user=None, **data):
		return services.apply_leave(
			self.student.name, leave_data(**data), applied_by, user or self.student.user
		)

	# ------------------------------------------------------------------ workflow

	def test_student_leave_goes_to_parent_then_warden(self):
		with capture_notifications() as sent:
			name = self.apply()
		leave = frappe.get_doc("Home Visit Leave", name)
		self.assertEqual(leave.status, "Pending Parent Approval")
		self.assertEqual(leave.parent_approval_status, "Pending")
		self.assertEqual(leave.hostel, self.hostel)
		self.assertEqual(leave.applied_by_user, self.student.user)
		self.assertIn("leave_submitted", sent.events())
		self.assertEqual(sent.recipients("leave_parent_approval_required"), {self.guardian.user})

		with capture_notifications() as sent:
			services.parent_decision(name, self.guardian.name, True, "Please send him by bus")
		leave.reload()
		self.assertEqual(leave.status, "Pending Warden Approval")
		self.assertEqual(leave.parent_guardian, self.guardian.name)
		self.assertTrue(leave.parent_action_on)
		self.assertIn(self.deputy, sent.recipients("leave_warden_approval_required"))
		self.assertIn(self.warden, sent.recipients("leave_warden_approval_required"))

		with capture_notifications() as sent:
			services.warden_decision(name, True, None, self.deputy)
		leave.reload()
		self.assertEqual(leave.status, "Approved")
		self.assertEqual(leave.warden_approval_status, "Approved")
		self.assertEqual(leave.warden_user, self.deputy)
		self.assertEqual(sent.recipients("leave_decided"), {self.student.user, self.guardian.user})

	def test_parent_step_skipped_when_settings_do_not_require_it(self):
		set_hostel_settings(leave_requires_parent_approval=0)
		leave = frappe.get_doc("Home Visit Leave", self.apply())
		self.assertEqual(leave.status, "Pending Warden Approval")
		self.assertEqual(leave.parent_approval_status, "Not Required")

	def test_parent_application_carries_parent_consent(self):
		leave = frappe.get_doc("Home Visit Leave", self.apply("Parent", self.guardian.user))
		self.assertEqual(leave.status, "Pending Warden Approval")
		self.assertEqual(leave.parent_approval_status, "Approved")
		self.assertEqual(leave.parent_guardian, self.guardian.name)

	def test_warden_application_skips_parent_step(self):
		office = make_user([OFFICE_ROLE])
		leave = frappe.get_doc("Home Visit Leave", self.apply("Warden", office))
		self.assertEqual(leave.status, "Pending Warden Approval")
		self.assertEqual(leave.parent_approval_status, "Not Required")

	def test_portal_user_cannot_apply_as_staff_or_for_another_student(self):
		with self.assertRaises(frappe.PermissionError):
			self.apply("Warden", self.student.user)
		other = make_student(self.hostel)
		with self.assertRaises(frappe.PermissionError):
			services.apply_leave(self.student.name, leave_data(), "Student", other.user)

	def test_parent_rejection_ends_the_request(self):
		name = self.apply()
		with self.assertRaises(frappe.ValidationError):
			services.parent_decision(name, self.guardian.name, False, "")
		with capture_notifications() as sent:
			services.parent_decision(name, self.guardian.name, False, "Exams next week")
		leave = frappe.get_doc("Home Visit Leave", name)
		self.assertEqual(leave.status, "Rejected")
		self.assertEqual(leave.parent_approval_status, "Rejected")
		self.assertIn("leave_decided", sent.events())
		with self.assertRaises(frappe.ValidationError):
			services.warden_decision(name, True, None, self.warden)

	def test_guardian_without_leave_rights_cannot_decide(self):
		other = make_guardian(self.student.name, can_approve_leave=0, relation="Brother")
		name = self.apply()
		with self.assertRaises(frappe.PermissionError):
			services.parent_decision(name, other.name, True, None)

	def test_parent_approval_needs_an_approving_guardian(self):
		lonely = make_student(self.hostel)
		with self.assertRaises(frappe.ValidationError):
			services.apply_leave(lonely.name, leave_data(), "Student", lonely.user)

	def test_warden_cannot_approve_before_parent(self):
		name = self.apply()
		with self.assertRaises(frappe.ValidationError):
			services.warden_decision(name, True, None, self.warden)
		# ... but may turn it down straight away
		services.warden_decision(name, False, "Not during exams", self.warden)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "status"), "Rejected")

	def test_rejection_needs_remarks(self):
		set_hostel_settings(leave_requires_parent_approval=0)
		name = self.apply()
		with self.assertRaises(frappe.ValidationError):
			services.warden_decision(name, False, "  ", self.warden)

	# ------------------------------------------------------------------ approvers

	def test_deputy_warden_is_scoped_to_their_hostel(self):
		set_hostel_settings(leave_requires_parent_approval=0)
		name = self.apply()
		other_deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(other_deputy, make_hostel())
		unscoped_deputy = make_user([DEPUTY_WARDEN_ROLE])
		office = make_user([OFFICE_ROLE])
		for user in (other_deputy, unscoped_deputy, office):
			with self.assertRaises(frappe.PermissionError):
				services.warden_decision(name, True, None, user)
		services.warden_decision(name, True, None, self.deputy)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "status"), "Approved")

	def test_chief_warden_approves_any_hostel(self):
		set_hostel_settings(leave_requires_parent_approval=0)
		name = self.apply()
		services.warden_decision(name, True, "OK", self.warden)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "warden_user"), self.warden)

	# ------------------------------------------------------------------ validation

	def test_return_must_be_after_leaving(self):
		data = leave_data()
		data["to_datetime"] = data["from_datetime"]
		with self.assertRaises(frappe.ValidationError):
			services.apply_leave(self.student.name, data, "Student", self.student.user)

	def test_leave_cannot_start_in_the_past_for_students(self):
		with self.assertRaises(frappe.ValidationError):
			self.apply(start_in_hours=-5)

	def test_overlapping_leave_is_refused(self):
		first = self.apply(start_in_hours=24, hours=72)
		with self.assertRaises(frappe.ValidationError):
			self.apply(start_in_hours=48, hours=48)
		# A later, separate leave is fine
		self.apply(start_in_hours=24 * 7, hours=24)
		# ... and a cancelled leave no longer holds the dates
		services.cancel_leave(first, self.student.user)
		self.apply(start_in_hours=48, hours=24)

	def test_only_active_validated_allotted_students_may_apply(self):
		cases = (
			make_student(self.hostel, status="Suspended"),
			make_student(self.hostel, validation_status="Pending"),
			make_student(None),
		)
		for student in cases:
			make_guardian(student.name)
			with self.assertRaises(frappe.ValidationError):
				services.apply_leave(student.name, leave_data(), "Student", student.user)

	def test_dates_are_locked_once_decided(self):
		set_hostel_settings(leave_requires_parent_approval=0)
		name = self.apply()
		services.warden_decision(name, True, None, self.warden)
		leave = frappe.get_doc("Home Visit Leave", name)
		leave.to_datetime = add_to_date(leave.to_datetime, days=2)
		with self.assertRaises(frappe.ValidationError):
			leave.save(ignore_permissions=True)

	# ------------------------------------------------------------------ movement

	def approved_leave(self, **data):
		set_hostel_settings(leave_requires_parent_approval=0)
		name = self.apply(**data)
		services.warden_decision(name, True, None, self.warden)
		return frappe.get_doc("Home Visit Leave", name)

	def test_check_out_and_return(self):
		leave = self.approved_leave(start_in_hours=1)
		frappe.set_user(self.deputy)
		with capture_notifications() as sent:
			leave.mark_checked_out()
		self.assertEqual(leave.status, "Checked Out")
		self.assertEqual(sent.recipients("leave_checked_out"), {self.guardian.user})
		with self.assertRaises(frappe.ValidationError):
			leave.mark_returned(returned_on=str(add_to_date(leave.checked_out_on, hours=-1)))
		with capture_notifications() as sent:
			leave.mark_returned()
		self.assertEqual(leave.status, "Returned")
		self.assertTrue(leave.returned_on)
		self.assertEqual(sent.recipients("leave_returned"), {self.guardian.user})
		self.assertEqual(sent.references("leave_returned"), [leave.name])

	def test_check_out_only_while_the_leave_is_on(self):
		early = self.approved_leave(start_in_hours=5)
		with self.assertRaises(frappe.ValidationError):
			early.record_check_out(self.warden)
		# A return cannot close a leave that has not started; it is cancelled instead.
		with self.assertRaises(frappe.ValidationError):
			early.record_return(self.warden)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", early.name, "status"), "Approved")

		# Up to two hours early is fine (an early bus).
		soon = self.approved_leave(start_in_hours=24 * 5)
		frappe.db.set_value(
			"Home Visit Leave", soon.name, "from_datetime", add_to_date(now_datetime(), minutes=90)
		)
		frappe.get_doc("Home Visit Leave", soon.name).record_check_out(self.warden)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", soon.name, "status"), "Checked Out")

	def test_movement_times_cannot_be_in_the_future(self):
		leave = self.approved_leave(start_in_hours=0.5)
		with self.assertRaises(frappe.ValidationError):
			leave.record_check_out(self.warden, add_to_date(now_datetime(), hours=2))
		leave.record_check_out(self.warden)
		with self.assertRaises(frappe.ValidationError):
			leave.record_return(self.warden, add_to_date(now_datetime(), hours=1))

	def test_gate_staff_read_leaves_and_record_movement_at_server_time(self):
		guard = make_user([SECURITY_ROLE])
		scope_to_hostel(guard, self.hostel)
		leave = self.approved_leave(start_in_hours=0.5)
		self.assertTrue(frappe.has_permission("Home Visit Leave", "read", doc=leave, user=guard))
		self.assertFalse(frappe.has_permission("Home Visit Leave", "write", doc=leave, user=guard))

		# Gate staff cannot choose the time, cancel, or decide.
		with self.assertRaises(frappe.PermissionError):
			self.as_user(guard, leave.mark_checked_out, str(add_to_date(now_datetime(), minutes=-30)))
		with self.assertRaises(frappe.PermissionError):
			self.as_user(
				guard, run_form_action, frappe.get_doc("Home Visit Leave", leave.name), "cancel_leave"
			)
		pending = services.apply_leave(make_student(self.hostel).name, leave_data(), "Warden", self.warden)
		with self.assertRaises(frappe.PermissionError):
			self.as_user(guard, frappe.get_doc("Home Visit Leave", pending).warden_approve, "ok")

		before = now_datetime()
		self.as_user(guard, leave.mark_checked_out)
		self.assertEqual(leave.status, "Checked Out")
		self.assertGreaterEqual(get_datetime(leave.checked_out_on), before.replace(microsecond=0))
		self.as_user(guard, leave.mark_returned)
		self.assertEqual(leave.status, "Returned")

	def test_read_only_staff_and_other_gates_cannot_record_movement(self):
		leave = self.approved_leave(start_in_hours=0.5)
		other_gate = make_user([SECURITY_ROLE])
		scope_to_hostel(other_gate, make_hostel())
		unposted_gate = make_user([SECURITY_ROLE])
		for user in (make_user([AUDITOR_ROLE]), make_user([MESS_MANAGER_ROLE]), other_gate, unposted_gate):
			with self.assertRaises(frappe.PermissionError, msg=user):
				self.as_user(user, frappe.get_doc("Home Visit Leave", leave.name).mark_checked_out)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", leave.name, "status"), "Approved")

		office = make_user([OFFICE_ROLE])
		self.as_user(office, frappe.get_doc("Home Visit Leave", leave.name).mark_checked_out)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", leave.name, "status"), "Checked Out")

	def test_portal_user_cannot_record_movement(self):
		leave = self.approved_leave()
		frappe.set_user(self.student.user)
		with self.assertRaises(frappe.PermissionError):
			leave.mark_checked_out()

	def test_cancel_rules(self):
		leave = self.approved_leave(start_in_hours=0.5)
		stranger = make_student(self.hostel)
		with self.assertRaises(frappe.PermissionError):
			services.cancel_leave(leave.name, stranger.user)
		leave.record_check_out(self.warden)
		with self.assertRaises(frappe.ValidationError):
			services.cancel_leave(leave.name, self.student.user)

		pending = self.apply(start_in_hours=24 * 10)
		services.cancel_leave(pending, self.guardian.user)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", pending, "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value("Home Visit Leave", pending, "cancelled_by"), self.guardian.user)

	def test_overdue_returns_are_flagged_after_grace(self):
		office = make_user([OFFICE_ROLE])
		start = add_to_date(now_datetime(), hours=-30)
		late = services.apply_leave(
			self.student.name,
			{**leave_data(), "from_datetime": start, "to_datetime": add_to_date(now_datetime(), hours=-3)},
			"Warden",
			office,
		)
		services.warden_decision(late, True, None, self.warden)
		frappe.get_doc("Home Visit Leave", late).record_check_out(self.warden, start)

		other = make_student(self.hostel)
		within_grace = services.apply_leave(
			other.name,
			{**leave_data(), "from_datetime": start, "to_datetime": add_to_date(now_datetime(), hours=-1)},
			"Warden",
			office,
		)
		services.warden_decision(within_grace, True, None, self.warden)

		with capture_notifications() as sent:
			mark_overdue_returns()
		self.assertEqual(frappe.db.get_value("Home Visit Leave", late, "status"), "Overdue")
		self.assertTrue(frappe.db.get_value("Home Visit Leave", late, "overdue_since"))
		self.assertEqual(frappe.db.get_value("Home Visit Leave", within_grace, "status"), "Approved")
		recipients = sent.recipients("leave_overdue")
		self.assertIn(self.deputy, recipients)
		self.assertIn(self.guardian.user, recipients)

		# Running again does not notify twice
		with capture_notifications() as sent:
			mark_overdue_returns()
		self.assertNotIn(late, sent.references())

		# Returning closes the overdue leave
		frappe.get_doc("Home Visit Leave", late).record_return(self.warden)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", late, "status"), "Returned")

	# ------------------------------------------------------------------ mess reduction

	def test_mess_reduction_window_counts_days_mostly_away(self):
		leave = frappe.new_doc("Home Visit Leave")
		leave.from_datetime = datetime(2026, 11, 6, 17, 0)  # leaves Friday evening
		leave.to_datetime = datetime(2026, 11, 10, 8, 0)  # back Tuesday morning
		first, last, days = leave.get_mess_reduction_window()
		self.assertEqual((str(first), str(last), days), ("2026-11-07", "2026-11-09", 3))

		leave.from_datetime = datetime(2026, 11, 6, 9, 0)  # leaves before lunch
		leave.to_datetime = datetime(2026, 11, 10, 20, 0)  # back after dinner time
		self.assertEqual(leave.get_mess_reduction_window()[2], 5)

		leave.from_datetime = datetime(2026, 11, 6, 18, 0)
		leave.to_datetime = datetime(2026, 11, 7, 9, 0)
		self.assertEqual(leave.get_mess_reduction_window(), (None, None, 0))

	def test_approval_raises_mess_reduction(self):
		set_hostel_settings(leave_requires_parent_approval=0)
		start = get_datetime(add_to_date(now_datetime(), days=3)).replace(
			hour=8, minute=0, second=0, microsecond=0
		)
		data = {
			"from_datetime": start,
			"to_datetime": add_to_date(start, days=5, hours=10),
			"apply_mess_reduction": 1,
		}
		name = services.apply_leave(self.student.name, {**leave_data(), **data}, "Student", self.student.user)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "mess_reduction_days"), 6)

		with patch(
			"hostel360.hostel_mess.services.apply_mess_reduction", return_value="MRA-TEST-0001"
		) as apply:
			services.warden_decision(name, True, None, self.warden)
		apply.assert_called_once()
		args, kwargs = apply.call_args
		self.assertEqual(args[0], self.student.name)
		self.assertEqual(str(args[1]), str(start.date()))
		self.assertEqual(args[3], "Home Visit")
		self.assertEqual(kwargs["home_visit_leave"], name)
		self.assertEqual(kwargs["user"], self.warden)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "mess_reduction"), "MRA-TEST-0001")

	def test_short_leave_skips_mess_reduction(self):
		set_hostel_settings(leave_requires_parent_approval=0)
		name = self.apply(hours=20, apply_mess_reduction=1)
		with patch("hostel360.hostel_mess.services.apply_mess_reduction") as apply:
			services.warden_decision(name, True, None, self.warden)
		apply.assert_not_called()
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "status"), "Approved")

	def test_mess_reduction_failure_does_not_block_approval(self):
		set_hostel_settings(leave_requires_parent_approval=0)
		name = self.apply(hours=24 * 6, apply_mess_reduction=1)

		def refuse(*args, **kwargs):
			frappe.throw("Mess period already billed")

		with patch("hostel360.hostel_mess.services.apply_mess_reduction", side_effect=refuse):
			services.warden_decision(name, True, None, self.warden)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "status"), "Approved")
		self.assertFalse(frappe.db.get_value("Home Visit Leave", name, "mess_reduction"))
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Home Visit Leave",
					"reference_name": name,
					"content": ["like", "%already billed%"],
				},
			)
		)

	# ------------------------------------------------------------------ lists and migration

	def test_lists_for_portal(self):
		name = self.apply()
		leaves = services.list_leaves(self.student.name)
		self.assertEqual([row.name for row in leaves], [name])
		self.assertTrue(leaves[0].can_cancel)
		pending = services.list_pending_parent_approvals(self.guardian.name)
		self.assertEqual([row.name for row in pending], [name])

	def test_migration_accepts_historical_leave(self):
		vacated = make_student(self.hostel, status="Vacated")
		frappe.flags.hostel360_migration = True
		try:
			with capture_notifications() as sent:
				leave = frappe.get_doc(
					{
						"doctype": "Home Visit Leave",
						"student": vacated.name,
						"from_datetime": "2025-01-10 08:00:00",
						"to_datetime": "2025-01-12 18:00:00",
						"destination": "Chennai",
						"reason": "Pongal",
						"applied_by": "Student",
						"status": "Returned",
						"parent_approval_status": "Approved",
						"warden_approval_status": "Approved",
						"hostel": self.hostel,
					}
				).insert(ignore_permissions=True)
		finally:
			frappe.flags.hostel360_migration = False
		self.assertEqual(leave.status, "Returned")
		self.assertEqual(sent, [])

	# ------------------------------------------------------------------ server-owned fields

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def other_hostel_leave(self):
		set_hostel_settings(leave_requires_parent_approval=0)
		other = make_student(make_hostel())
		return other, services.apply_leave(other.name, leave_data(), "Student", other.user)

	def test_scoped_staff_cannot_move_a_leave_into_their_hostel(self):
		other, name = self.other_hostel_leave()
		office = make_user([OFFICE_ROLE])
		scope_to_hostel(office, self.hostel)
		leave = frappe.get_doc("Home Visit Leave", name)
		self.assertFalse(frappe.has_permission("Home Visit Leave", "read", doc=leave, user=office))

		for hostel in (self.hostel, ""):
			with self.assertRaises(frappe.PermissionError):
				self.as_user(office, client_set_value, "Home Visit Leave", name, {"hostel": hostel})
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "hostel"), other.hostel)

		# Nor create one for another hostel's student by naming their own hostel
		with self.assertRaises(frappe.PermissionError):
			self.as_user(
				office,
				client_insert,
				{
					"doctype": "Home Visit Leave",
					"student": other.name,
					"hostel": self.hostel,
					"applied_by": "Warden",
					**leave_data(),
				},
			)

		# Stored hostel decides, whatever the in-memory copy says
		leave.hostel = self.hostel
		self.assertFalse(hostel_scoped_has_permission(leave, "read", office))

	def test_an_ordinary_save_cannot_decide_a_leave(self):
		name = self.apply()
		office = make_user([OFFICE_ROLE])
		decision = {
			"status": "Approved",
			"parent_approval_status": "Approved",
			"warden_approval_status": "Approved",
		}
		with self.assertRaises(frappe.PermissionError):
			self.as_user(office, client_set_value, "Home Visit Leave", name, decision)
		# Not even a warden can skip the parent's consent by saving the form
		with self.assertRaises(frappe.PermissionError):
			self.as_user(
				self.warden,
				client_set_value,
				"Home Visit Leave",
				name,
				{**decision, "warden_user": self.warden},
			)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "status"), "Pending Parent Approval")

		# Ordinary details can still be corrected while the leave is pending
		self.as_user(office, client_set_value, "Home Visit Leave", name, {"destination": "Sivaganga"})
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "destination"), "Sivaganga")

	def test_a_new_leave_carries_no_decision(self):
		leave = frappe.get_doc(
			{
				"doctype": "Home Visit Leave",
				"student": self.student.name,
				"applied_by": "Warden",
				"status": "Approved",
				"warden_approval_status": "Approved",
				"warden_user": self.warden,
				"checked_out_on": now_datetime(),
				**leave_data(),
			}
		).insert()
		self.assertEqual(leave.status, "Pending Warden Approval")
		self.assertEqual(leave.warden_approval_status, "Pending")
		self.assertFalse(leave.warden_user)
		self.assertFalse(leave.checked_out_on)

	def test_form_actions_work_on_the_stored_leave(self):
		name = self.apply()
		# A warden's form claims the parent has already approved
		form_copy = frappe.get_doc("Home Visit Leave", name)
		form_copy.status = "Pending Warden Approval"
		with self.assertRaises(frappe.ValidationError):
			self.as_user(self.warden, run_form_action, form_copy, "warden_approve", remarks="ok")
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "status"), "Pending Parent Approval")

		# A deputy of another hostel rewrites the hostel on the form's copy
		other, other_leave = self.other_hostel_leave()
		form_copy = frappe.get_doc("Home Visit Leave", other_leave)
		form_copy.hostel = self.hostel
		with self.assertRaises(frappe.PermissionError):
			self.as_user(self.deputy, run_form_action, form_copy, "warden_approve", remarks="ok")
		self.assertEqual(
			frappe.db.get_value("Home Visit Leave", other_leave, ["status", "hostel"]),
			("Pending Warden Approval", other.hostel),
		)

		# The genuine action still works and refreshes the form's copy
		form_copy = frappe.get_doc("Home Visit Leave", other_leave)
		self.as_user(self.warden, form_copy.warden_approve, "Safe journey")
		self.assertEqual(form_copy.status, "Approved")

	# ------------------------------------------------------------------ cancelling with a reduction

	def test_office_can_cancel_a_leave_with_a_mess_reduction(self):
		leave = self.approved_leave()
		reduction = make_linked_reduction(leave.name, status="Pending")
		office = make_user([OFFICE_ROLE])
		self.assertFalse(frappe.has_permission("Mess Reduction Application", "write", user=office))

		self.as_user(office, run_form_action, frappe.get_doc("Home Visit Leave", leave.name), "cancel_leave")
		self.assertEqual(frappe.db.get_value("Home Visit Leave", leave.name, "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value("Mess Reduction Application", reduction, "status"), "Cancelled")

	def test_cancelling_a_started_leave_withdraws_its_reduction(self):
		start = get_datetime(nowdate()).replace(hour=0, minute=30)
		name = services.apply_leave(
			self.student.name,
			{**leave_data(), "from_datetime": start, "to_datetime": add_to_date(start, days=4)},
			"Warden",
			self.warden,
		)
		services.warden_decision(name, True, None, self.warden)
		reduction = make_linked_reduction(name, from_date=nowdate(), status="Approved")

		services.cancel_leave(name, self.student.user)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", name, "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value("Mess Reduction Application", reduction, "status"), "Cancelled")

	def test_reduction_that_cannot_be_withdrawn_is_reported(self):
		leave = self.approved_leave()
		reduction = make_linked_reduction(leave.name, status="Approved")
		mess_manager = make_user([MESS_MANAGER_ROLE])

		def billed(*args, **kwargs):
			frappe.throw("Mess bills are published up to the end of the month.")

		with patch("hostel360.hostel_mess.services.cancel_mess_reduction", side_effect=billed):
			services.cancel_leave(leave.name, self.student.user)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", leave.name, "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value("Mess Reduction Application", reduction, "status"), "Approved")
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Home Visit Leave",
					"reference_name": leave.name,
					"content": ["like", "%published up to%"],
				},
			)
		)
		alerted = set(
			frappe.get_all(
				"Notification Log",
				filters={"document_type": "Mess Reduction Application", "document_name": reduction},
				pluck="for_user",
			)
		)
		self.assertIn(self.warden, alerted)
		self.assertIn(self.deputy, alerted)
		self.assertIn(mess_manager, alerted)

	# ------------------------------------------------------------------ hostels and blocks

	def test_vacated_student_keeps_their_last_hostel_on_records(self):
		if not frappe.get_meta("Hostel Student").has_field("last_hostel"):
			self.skipTest("Hostel Student has no last_hostel on this site")
		from hostel360.student_services.utils import get_student_hostel

		vacated = make_student(None)
		frappe.db.set_value("Hostel Student", vacated.name, "last_hostel", self.hostel, update_modified=False)
		self.assertEqual(get_student_hostel(vacated.name), self.hostel)

	def test_block_deputies_decide_only_for_their_blocks(self):
		if not people_service("staff_covers_student"):
			self.skipTest("Hostel People does not provide block scoping on this site")
		set_hostel_settings(leave_requires_parent_approval=0)
		east, west = make_block_room(self.hostel), make_block_room(self.hostel)
		put_in_room(self.student.name, east.room)
		westerner = make_student(self.hostel)
		put_in_room(westerner.name, west.room)
		east_deputy = make_block_deputy(self.hostel, [east.block])

		with capture_notifications() as sent:
			mine = self.apply()
			theirs = services.apply_leave(westerner.name, leave_data(), "Student", westerner.user)
		told = {
			m["reference_name"]: {r.get("user") for r in m["recipients"]}
			for m in sent
			if m["event"] == "leave_warden_approval_required"
		}
		self.assertIn(east_deputy, told[mine])
		self.assertNotIn(east_deputy, told[theirs])
		# A deputy assigned to the whole hostel hears about both
		self.assertIn(self.deputy, told[theirs])

		with self.assertRaises(frappe.PermissionError):
			services.warden_decision(theirs, True, None, east_deputy)
		services.warden_decision(mine, True, None, east_deputy)
		self.assertEqual(frappe.db.get_value("Home Visit Leave", mine, "status"), "Approved")
