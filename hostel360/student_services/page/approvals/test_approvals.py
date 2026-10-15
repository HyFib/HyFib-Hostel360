# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from hostel360.setup.roles import DEPUTY_WARDEN_ROLE, MESS_MANAGER_ROLE, OFFICE_ROLE, WARDEN_ROLE
from hostel360.student_services import services
from hostel360.student_services.page.approvals import approvals
from hostel360.student_services.testing import (
	leave_data,
	make_block_deputy,
	make_block_room,
	make_hostel,
	make_student,
	make_user,
	put_in_room,
	scope_to_hostel,
	set_hostel_settings,
)
from hostel360.student_services.utils import people_service


class TestApprovals(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_hostel_settings(leave_requires_parent_approval=0)
		self.hostel = make_hostel()
		self.other_hostel = make_hostel()
		self.deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(self.deputy, self.hostel)
		self.warden = make_user([WARDEN_ROLE])

		self.student = make_student(self.hostel)
		self.other = make_student(self.other_hostel)
		self.leave = services.apply_leave(self.student.name, leave_data(), "Student", self.student.user)
		self.other_leave = services.apply_leave(self.other.name, leave_data(), "Student", self.other.user)
		self.certificate = services.request_bonafide(self.student.name, "Passport", None)

	def tearDown(self):
		frappe.set_user("Administrator")

	def inbox_as(self, user):
		frappe.set_user(user)
		try:
			return approvals.get_inbox()
		finally:
			frappe.set_user("Administrator")

	def names(self, inbox, section):
		return {row.name for row in inbox["sections"].get(section, [])}

	def test_deputy_sees_only_their_hostel(self):
		inbox = self.inbox_as(self.deputy)
		self.assertIn(self.leave, self.names(inbox, "leaves"))
		self.assertNotIn(self.other_leave, self.names(inbox, "leaves"))
		self.assertIn(self.certificate, self.names(inbox, "certificates"))
		self.assertEqual(inbox["counts"]["leaves"], len(inbox["sections"]["leaves"]))

	def test_warden_sees_every_hostel(self):
		inbox = self.inbox_as(self.warden)
		self.assertTrue({self.leave, self.other_leave} <= self.names(inbox, "leaves"))

	def test_sections_follow_what_the_user_may_decide(self):
		mess_manager = make_user([MESS_MANAGER_ROLE])
		inbox = self.inbox_as(mess_manager)
		self.assertNotIn("leaves", inbox["sections"])
		self.assertNotIn("certificates", inbox["sections"])
		unscoped_deputy = make_user([DEPUTY_WARDEN_ROLE])
		self.assertEqual(self.inbox_as(unscoped_deputy)["sections"]["leaves"], [])
		office = make_user([OFFICE_ROLE])
		self.assertNotIn("leaves", self.inbox_as(office)["sections"])

	def test_decide_leave_and_certificate_inline(self):
		frappe.set_user(self.deputy)
		result = approvals.decide("Home Visit Leave", self.leave, "approve", "Safe journey")
		self.assertEqual(result["status"], "Approved")
		with self.assertRaises(frappe.ValidationError):
			approvals.decide("Bonafide Certificate Request", self.certificate, "reject", "")
		self.assertEqual(
			approvals.decide("Bonafide Certificate Request", self.certificate, "approve")["status"], "Approved"
		)
		with self.assertRaises(frappe.PermissionError):
			approvals.decide("Home Visit Leave", self.other_leave, "approve")
		with self.assertRaises(frappe.ValidationError):
			approvals.decide("User", self.deputy, "approve")
		frappe.set_user("Administrator")
		self.assertNotIn(self.leave, self.names(self.inbox_as(self.deputy), "leaves"))

	def test_mess_reductions_are_decided_by_the_mess_service(self):
		frappe.set_user(self.warden)
		with patch("hostel360.hostel_mess.services.decide_mess_reduction") as decide:
			approvals.decide("Mess Reduction Application", "MRA-TEST-1", "reject", "Billing closed")
		decide.assert_called_once_with("MRA-TEST-1", False, "Billing closed", self.warden)

	def test_escalated_grievances_need_attention(self):
		name = services.raise_grievance(
			self.student.name,
			{"category": "Water", "subject": "No water on second floor", "description": "Since morning"},
			self.student.user,
		)
		frappe.db.set_value("Hostel Grievance", name, "sla_due_by", add_to_date(now_datetime(), hours=-1))
		from hostel360.student_services.grievance_tasks import escalate_overdue_grievances

		escalate_overdue_grievances()
		self.assertIn(name, self.names(self.inbox_as(self.deputy), "grievances"))

		frappe.set_user(self.deputy)
		self.assertEqual(approvals.act_on_grievance(name, "take")["status"], "Escalated")
		self.assertEqual(frappe.db.get_value("Hostel Grievance", name, "assigned_to"), self.deputy)
		with self.assertRaises(frappe.ValidationError):
			approvals.act_on_grievance(name, "resolve", "")
		self.assertEqual(approvals.act_on_grievance(name, "resolve", "Motor repaired")["status"], "Resolved")
		frappe.set_user("Administrator")
		self.assertNotIn(name, self.names(self.inbox_as(self.deputy), "grievances"))

	def test_block_deputy_sees_and_decides_only_their_blocks(self):
		if not people_service("staff_covers_student"):
			self.skipTest("Hostel People does not provide block scoping on this site")
		east, west = make_block_room(self.hostel), make_block_room(self.hostel)
		put_in_room(self.student.name, east.room)
		westerner = make_student(self.hostel)
		put_in_room(westerner.name, west.room)
		west_leave = services.apply_leave(westerner.name, leave_data(), "Student", westerner.user)
		west_certificate = services.request_bonafide(westerner.name, "Visa", None)
		east_deputy = make_block_deputy(self.hostel, [east.block])

		inbox = self.inbox_as(east_deputy)
		self.assertIn(self.leave, self.names(inbox, "leaves"))
		self.assertNotIn(west_leave, self.names(inbox, "leaves"))
		self.assertIn(self.certificate, self.names(inbox, "certificates"))
		self.assertNotIn(west_certificate, self.names(inbox, "certificates"))
		# The deputy of the whole hostel still sees both blocks
		self.assertIn(west_leave, self.names(self.inbox_as(self.deputy), "leaves"))

		frappe.set_user(east_deputy)
		with self.assertRaises(frappe.PermissionError):
			approvals.decide("Bonafide Certificate Request", west_certificate, "approve")
		self.assertEqual(approvals.decide("Home Visit Leave", self.leave, "approve")["status"], "Approved")


	def make_pending_indent(self, raised_by: str, requested_by: str | None = None) -> str:
		# Indents belong to Hostel Mess; only a submitted indent awaiting approval matters here.
		doc = frappe.new_doc("Mess Indent")
		doc.update(
			{
				"naming_series": "IND-.YYYY.-.#####",
				"mess": "Mess " + frappe.generate_hash(length=6),
				"posting_date": frappe.utils.nowdate(),
				"required_by": frappe.utils.add_days(frappe.utils.nowdate(), 2),
				"status": "Pending Approval",
				"requested_by": requested_by or raised_by,
			}
		)
		doc.name = "IND-TEST-" + frappe.generate_hash(length=8)
		doc.owner = raised_by
		doc.docstatus = 1
		doc.db_insert()
		return doc.name

	def test_indents_raised_by_the_viewer_wait_for_someone_else(self):
		if not frappe.db.exists("DocType", "Mess Indent"):
			self.skipTest("Hostel Mess is not installed on this site")
		manager = make_user([MESS_MANAGER_ROLE])
		colleague = make_user([MESS_MANAGER_ROLE])
		own = self.make_pending_indent(manager)
		on_behalf = self.make_pending_indent(colleague, requested_by=manager)
		theirs = self.make_pending_indent(colleague)

		mine = self.names(self.inbox_as(manager), "indents")
		self.assertNotIn(own, mine)
		self.assertNotIn(on_behalf, mine)
		self.assertIn(theirs, mine)
		self.assertIn(own, self.names(self.inbox_as(colleague), "indents"))
		# Wardens may decide indents whoever raised them.
		self.assertTrue({own, on_behalf, theirs} <= self.names(self.inbox_as(self.warden), "indents"))
