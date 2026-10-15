# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import random

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime, time_diff_in_hours

from hostel360.setup.roles import (
	ADMIN_ROLE,
	AUDITOR_ROLE,
	DEPUTY_WARDEN_ROLE,
	MESS_MANAGER_ROLE,
	OFFICE_ROLE,
	WARDEN_ROLE,
)
from hostel360.student_services import services
from hostel360.student_services.grievance_tasks import escalate_overdue_grievances
from hostel360.student_services.permissions import grievance_has_permission
from hostel360.student_services.testing import (
	capture_notifications,
	make_guardian,
	make_hostel,
	make_student,
	make_user,
	scope_to_hostel,
	set_hostel_settings,
)

IGNORE_TEST_RECORD_DEPENDENCIES = ["Hostel Student", "Hostel", "Hostel Room", "User"]


class TestHostelGrievance(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_hostel_settings(grievance_escalation_hours=48, grievance_urgent_escalation_hours=4)
		self.hostel = make_hostel()
		self.student = make_student(self.hostel)
		self.warden = make_user([WARDEN_ROLE])
		self.admin = make_user([ADMIN_ROLE])
		self.deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(self.deputy, self.hostel)

	def tearDown(self):
		frappe.set_user("Administrator")

	def raise_grievance(self, student=None, **data):
		student = student or self.student
		values = {
			"category": "Electrical",
			"subject": "Fan not working",
			"description": "The ceiling fan in my room stopped working yesterday.",
			"priority": "Medium",
			**data,
		}
		return services.raise_grievance(student.name, values, student.user)

	def visible_to(self, user, names):
		frappe.set_user(user)
		try:
			return set(frappe.get_list("Hostel Grievance", filters={"name": ["in", names]}, pluck="name"))
		finally:
			frappe.set_user("Administrator")

	# ------------------------------------------------------------------ creation

	def test_sla_follows_priority(self):
		normal = frappe.get_doc("Hostel Grievance", self.raise_grievance())
		urgent = frappe.get_doc("Hostel Grievance", self.raise_grievance(priority="Urgent"))
		self.assertAlmostEqual(time_diff_in_hours(normal.sla_due_by, normal.creation), 48, delta=0.1)
		self.assertAlmostEqual(time_diff_in_hours(urgent.sla_due_by, urgent.creation), 4, delta=0.1)
		self.assertEqual(normal.status, "Open")
		self.assertEqual(normal.hostel, self.hostel)
		self.assertEqual(normal.raised_by_user, self.student.user)
		self.assertEqual(len(normal.updates), 1)

	def test_ragging_is_confidential_urgent_and_escalated_at_once(self):
		with capture_notifications() as sent:
			name = self.raise_grievance(category="Ragging or Harassment", priority="Low", is_confidential=0)
		doc = frappe.get_doc("Hostel Grievance", name)
		self.assertEqual(doc.is_confidential, 1)
		self.assertEqual(doc.priority, "Urgent")
		self.assertEqual(doc.status, "Escalated")
		self.assertEqual(doc.escalation_level, 1)
		self.assertTrue(doc.escalated_on)
		escalated_to = sent.recipients("grievance_escalated")
		self.assertIn(self.warden, escalated_to)
		self.assertIn(self.admin, escalated_to)
		# Deputy wardens are not told about confidential complaints, and messages to staff omit the name.
		self.assertNotIn(self.deputy, sent.recipients("grievance_received"))
		staff_message = next(m for m in sent if m["event"] == "grievance_escalated")
		self.assertEqual(staff_message["context"]["student_name"], "")

	def test_mess_complaints_reach_mess_managers(self):
		mess_manager = make_user([MESS_MANAGER_ROLE])
		with capture_notifications() as sent:
			self.raise_grievance(category="Mess and Food", subject="Undercooked rice")
		self.assertIn(mess_manager, sent.recipients("grievance_received"))
		self.assertIn(self.deputy, sent.recipients("grievance_received"))
		self.assertIn(self.student.user, sent.recipients("grievance_received"))

	def test_attachments_must_be_the_callers_own_private_uploads(self):
		own = self.make_file(self.student.user)
		someone_else = self.make_file(self.warden)
		with self.assertRaises(frappe.ValidationError):
			services.raise_grievance(
				self.student.name,
				{"category": "Water", "subject": "No water", "description": "Taps dry"},
				self.student.user,
				[someone_else],
			)
		name = services.raise_grievance(
			self.student.name,
			{"category": "Water", "subject": "No water", "description": "Taps dry since morning"},
			self.student.user,
			[own],
		)
		self.assertEqual(
			frappe.db.get_value("File", {"file_url": own}, ["attached_to_doctype", "attached_to_name"]),
			("Hostel Grievance", name),
		)
		self.assertEqual(len(services.get_grievance(name)["attachments"]), 1)

	def make_file(self, owner):
		from io import BytesIO

		from PIL import Image

		# Distinct pixels per file: Frappe shares one stored file between identical uploads.
		buffer = BytesIO()
		colour = tuple(random.randrange(256) for _channel in range(3))
		Image.new("RGB", (4, 4), colour).save(buffer, format="PNG")
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"photo-{frappe.generate_hash(length=6)}.png",
				"content": buffer.getvalue(),
				"is_private": 1,
			}
		)
		file.insert(ignore_permissions=True)
		frappe.db.set_value("File", file.name, "owner", owner, update_modified=False)
		return file.file_url

	# ------------------------------------------------------------------ permissions

	def test_confidential_grievances_are_hidden_from_other_staff(self):
		open_one = self.raise_grievance()
		confidential = self.raise_grievance(subject="Senior threatening me", category="Ragging or Harassment")
		names = [open_one, confidential]
		office = make_user([OFFICE_ROLE])
		auditor = make_user([AUDITOR_ROLE])

		self.assertEqual(self.visible_to(self.warden, names), set(names))
		self.assertEqual(self.visible_to(self.admin, names), set(names))
		self.assertEqual(self.visible_to(self.deputy, names), {open_one})
		self.assertEqual(self.visible_to(office, names), {open_one})
		self.assertEqual(self.visible_to(auditor, names), {open_one})

		doc = frappe.get_doc("Hostel Grievance", confidential)
		self.assertFalse(grievance_has_permission(doc, "read", self.deputy))
		self.assertTrue(grievance_has_permission(doc, "read", self.warden))
		self.assertFalse(frappe.has_permission("Hostel Grievance", "read", doc=doc, user=office))

		# Assigning it makes it visible to that staff member only
		doc.assign_to_user(office, self.warden)
		self.assertEqual(self.visible_to(office, names), set(names))
		self.assertTrue(frappe.has_permission("Hostel Grievance", "read", doc=doc, user=office))
		self.assertEqual(self.visible_to(self.deputy, names), {open_one})

	def test_deputy_sees_only_their_hostels(self):
		other_student = make_student(make_hostel())
		mine = self.raise_grievance()
		theirs = self.raise_grievance(student=other_student)
		self.assertEqual(self.visible_to(self.deputy, [mine, theirs]), {mine})

	def test_mess_manager_sees_only_mess_and_food(self):
		mess_manager = make_user([MESS_MANAGER_ROLE])
		mess = self.raise_grievance(category="Mess and Food", subject="Stale chapati")
		electrical = self.raise_grievance()
		self.assertEqual(self.visible_to(mess_manager, [mess, electrical]), {mess})
		doc = frappe.get_doc("Hostel Grievance", electrical)
		self.assertFalse(grievance_has_permission(doc, "read", mess_manager))

	# ------------------------------------------------------------------ handling

	def test_staff_workflow_and_public_timeline(self):
		name = self.raise_grievance()
		doc = frappe.get_doc("Hostel Grievance", name)
		electrician = make_user([OFFICE_ROLE], first_name="Murugan")

		with capture_notifications() as sent:
			doc.assign_to_user(electrician, self.deputy, "Check the regulator too")
		self.assertEqual(doc.status, "In Progress")
		self.assertEqual(doc.assigned_to, electrician)
		self.assertIn(electrician, sent.recipients("grievance_assigned"))

		doc.add_staff_update("Electrician visited; part ordered.", self.deputy)
		doc.add_staff_update("Part costs Rs 450 from stores budget.", self.deputy, is_internal=1)

		detail = services.get_grievance(name)
		messages = [entry["message"] for entry in detail["timeline"]]
		self.assertIn("Electrician visited; part ordered.", messages)
		self.assertNotIn("Part costs Rs 450 from stores budget.", messages)
		self.assertNotIn("Check the regulator too", messages)
		self.assertTrue(detail["assigned"])
		self.assertFalse(detail["can_rate"])

		with capture_notifications() as sent:
			doc.resolve_by("Fan regulator replaced.", electrician)
		self.assertEqual(doc.status, "Resolved")
		self.assertEqual(doc.resolved_by, electrician)
		self.assertGreaterEqual(doc.resolution_hours, 0)
		self.assertFalse(doc.sla_breached)
		self.assertEqual(sent.recipients("grievance_resolved"), {self.student.user})

		doc.close_by(self.deputy)
		self.assertEqual(doc.status, "Closed")
		with self.assertRaises(frappe.ValidationError):
			services.add_grievance_comment(name, "Still broken", self.student.user)

	def test_staff_without_rights_cannot_act(self):
		name = self.raise_grievance()
		doc = frappe.get_doc("Hostel Grievance", name)
		mess_manager = make_user([MESS_MANAGER_ROLE])
		with self.assertRaises(frappe.PermissionError):
			doc.resolve_by("Done", mess_manager)
		with self.assertRaises(frappe.PermissionError):
			doc.assign_to_user(self.deputy, self.student.user)

	def test_closing_without_resolution_needs_a_reason(self):
		doc = frappe.get_doc("Hostel Grievance", self.raise_grievance())
		with self.assertRaises(frappe.ValidationError):
			doc.close_by(self.warden, "")
		doc.close_by(self.warden, "Duplicate of an earlier complaint")
		self.assertEqual(doc.status, "Closed")

	def test_reopen_within_seven_days_only(self):
		name = self.raise_grievance()
		doc = frappe.get_doc("Hostel Grievance", name)
		doc.resolve_by("Fixed", self.warden)

		guardian = make_guardian(self.student.name)
		services.reopen_grievance(name, "Fan stopped again", guardian.user)
		doc.reload()
		self.assertEqual(doc.status, "Reopened")
		self.assertEqual(doc.reopen_count, 1)
		self.assertGreater(get_datetime(doc.sla_due_by), now_datetime())

		doc.resolve_by("Replaced the capacitor", self.warden)
		frappe.db.set_value("Hostel Grievance", name, "resolved_on", add_days(now_datetime(), -8))
		with self.assertRaises(frappe.ValidationError):
			services.reopen_grievance(name, "Again", self.student.user)

		stranger = make_student(self.hostel)
		with self.assertRaises(frappe.PermissionError):
			services.reopen_grievance(name, "Not mine", stranger.user)

	def test_rating_after_resolution(self):
		name = self.raise_grievance()
		with self.assertRaises(frappe.ValidationError):
			services.rate_grievance(name, 4, None, self.student.user)
		frappe.get_doc("Hostel Grievance", name).resolve_by("Fixed", self.warden)
		with self.assertRaises(frappe.ValidationError):
			services.rate_grievance(name, 6, None, self.student.user)
		services.rate_grievance(name, 4, "Quick work", self.student.user)
		detail = services.get_grievance(name)
		self.assertEqual(detail["student_rating"], 4)
		self.assertEqual(detail["student_feedback"], "Quick work")
		self.assertEqual(services.list_grievances(self.student.name)[0]["student_rating"], 4)

	def test_student_comment_notifies_handlers(self):
		name = self.raise_grievance()
		with capture_notifications() as sent:
			services.add_grievance_comment(name, "It is sparking now, please hurry", self.student.user)
		self.assertIn(self.deputy, sent.recipients("grievance_updated"))
		timeline = services.get_grievance(name)["timeline"]
		self.assertEqual(timeline[-1]["message"], "It is sparking now, please hurry")
		self.assertFalse(timeline[-1]["is_staff"])

	# ------------------------------------------------------------------ escalation

	def test_overdue_grievances_escalate_one_level_per_period(self):
		name = self.raise_grievance()
		frappe.db.set_value("Hostel Grievance", name, "sla_due_by", add_to_date(now_datetime(), hours=-1))
		fresh = self.raise_grievance(subject="Light flickering")

		with capture_notifications() as sent:
			escalate_overdue_grievances()
		doc = frappe.get_doc("Hostel Grievance", name)
		self.assertEqual(doc.status, "Escalated")
		self.assertEqual(doc.escalation_level, 1)
		self.assertTrue(doc.sla_breached)
		self.assertGreater(get_datetime(doc.sla_due_by), now_datetime())
		self.assertEqual(sent.references("grievance_escalated"), [name])
		self.assertIn(self.deputy, sent.recipients("grievance_escalated"))
		self.assertIn(self.warden, sent.recipients("grievance_escalated"))
		self.assertEqual(frappe.db.get_value("Hostel Grievance", fresh, "status"), "Open")

		# Not again until the next period has passed
		with capture_notifications() as sent:
			escalate_overdue_grievances()
		self.assertEqual(sent.references("grievance_escalated"), [])

		# Second escalation goes to the Warden and administrators, not the deputies
		frappe.db.set_value("Hostel Grievance", name, "sla_due_by", add_to_date(now_datetime(), hours=-1))
		with capture_notifications() as sent:
			escalate_overdue_grievances()
		self.assertEqual(frappe.db.get_value("Hostel Grievance", name, "escalation_level"), 2)
		self.assertNotIn(self.deputy, sent.recipients("grievance_escalated"))
		self.assertIn(self.admin, sent.recipients("grievance_escalated"))

		# The level stops climbing at the top of the matrix
		for _attempt in range(3):
			frappe.db.set_value("Hostel Grievance", name, "sla_due_by", add_to_date(now_datetime(), hours=-1))
			escalate_overdue_grievances()
		self.assertEqual(frappe.db.get_value("Hostel Grievance", name, "escalation_level"), 3)

	def test_resolved_grievances_do_not_escalate(self):
		name = self.raise_grievance()
		frappe.get_doc("Hostel Grievance", name).resolve_by("Fixed", self.warden)
		frappe.db.set_value("Hostel Grievance", name, "sla_due_by", add_to_date(now_datetime(), hours=-5))
		escalate_overdue_grievances()
		self.assertEqual(frappe.db.get_value("Hostel Grievance", name, "status"), "Resolved")

	def test_resolving_late_marks_sla_breach(self):
		name = self.raise_grievance()
		frappe.db.set_value("Hostel Grievance", name, "sla_due_by", add_to_date(now_datetime(), hours=-1))
		doc = frappe.get_doc("Hostel Grievance", name)
		doc.resolve_by("Fixed late", self.warden)
		self.assertTrue(doc.sla_breached)

	def test_migration_keeps_historical_state(self):
		frappe.flags.hostel360_migration = True
		try:
			with capture_notifications() as sent:
				doc = frappe.get_doc(
					{
						"doctype": "Hostel Grievance",
						"student": self.student.name,
						"category": "Plumbing",
						"subject": "Leaking tap",
						"description": "Old complaint",
						"priority": "Low",
						"status": "Closed",
						"resolution": "Washer replaced",
					}
				).insert(ignore_permissions=True)
		finally:
			frappe.flags.hostel360_migration = False
		self.assertEqual(doc.status, "Closed")
		self.assertEqual(sent, [])
