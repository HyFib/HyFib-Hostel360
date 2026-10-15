# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import random

import frappe
from frappe import _
from frappe.client import insert as client_insert
from frappe.client import set_value as client_set_value
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
from hostel360.student_services.doctype.hostel_grievance.hostel_grievance import staff_user_query
from hostel360.student_services.grievance_tasks import escalate_overdue_grievances
from hostel360.student_services.permissions import grievance_has_permission
from hostel360.student_services.testing import (
	capture_notifications,
	make_guardian,
	make_hostel,
	make_student,
	make_user,
	run_form_action,
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

	def test_system_entries_are_stored_in_english_and_read_in_the_readers_language(self):
		electrician = make_user([OFFICE_ROLE], first_name="Murugan")
		lang = frappe.local.lang
		frappe.local.lang = "ta"
		try:
			name = self.raise_grievance()
			doc = frappe.get_doc("Hostel Grievance", name)
			doc.assign_to_user(electrician, self.deputy)
			doc.resolve_by("Regulator replaced", electrician)
			doc.close_by(self.deputy)
		finally:
			frappe.local.lang = lang

		rows = frappe.get_all(
			"Hostel Grievance Update",
			filters={"parent": name, "parenttype": "Hostel Grievance"},
			fields=["name", "message", "message_template", "message_args"],
			order_by="idx asc",
		)
		assignee = frappe.utils.get_fullname(electrician)
		self.assertEqual(rows[0].message, "Grievance registered.")
		self.assertEqual(rows[0].message_template, "Grievance registered.")
		self.assertEqual(rows[1].message, f"Assigned to {assignee}.")
		self.assertEqual(rows[1].message_template, "Assigned to {0}.")
		self.assertEqual(frappe.parse_json(rows[1].message_args), [assignee])
		self.assertEqual(rows[2].message, "Regulator replaced")
		self.assertFalse(rows[2].message_template)
		self.assertEqual(rows[3].message, "Grievance closed.")

		frappe.local.lang = "ta"
		try:
			timeline = services.get_grievance(name)["timeline"]
			expected = [
				_("Grievance registered."),
				_("Assigned to {0}.").format(assignee),
				"Regulator replaced",
				_("Grievance closed."),
			]
		finally:
			frappe.local.lang = lang
		self.assertEqual([entry["display_message"] for entry in timeline], expected)
		self.assertEqual([entry["message"] for entry in timeline], [row.message for row in rows])
		self.assertEqual([entry["id"] for entry in timeline], [row.name for row in rows])

	def test_older_system_entries_are_tagged_for_translation(self):
		from hostel360.student_services.setup import tag_system_timeline_entries

		name = self.raise_grievance()
		frappe.db.set_value(
			"Hostel Grievance Update",
			{"parent": name, "idx": 1},
			{"message_template": None, "message_args": None},
			update_modified=False,
		)
		doc = frappe.get_doc("Hostel Grievance", name)
		doc.escalate()
		frappe.db.set_value(
			"Hostel Grievance Update",
			{"parent": name, "idx": 2},
			{"message_template": None, "message_args": None},
			update_modified=False,
		)

		tag_system_timeline_entries()
		rows = frappe.get_all(
			"Hostel Grievance Update",
			filters={"parent": name},
			fields=["message_template", "message_args"],
			order_by="idx asc",
		)
		self.assertEqual(rows[0].message_template, "Grievance registered.")
		self.assertEqual(rows[1].message_template, "Not resolved in time; escalated to level {0}.")
		self.assertEqual(frappe.parse_json(rows[1].message_args), ["1"])

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

	# ------------------------------------------------------------------ tampering

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def stored(self, name, *fields):
		return frappe.db.get_value("Hostel Grievance", name, list(fields), as_dict=True)

	def test_staff_cannot_open_a_confidential_grievance_by_rewriting_it(self):
		name = self.raise_grievance(category="Ragging or Harassment", description="SECRET: senior in room 12")
		office = make_user([OFFICE_ROLE])
		for values in ({"assigned_to": office}, {"category": "Other", "is_confidential": 0}, {"is_confidential": 0}):
			with self.assertRaises(frappe.PermissionError):
				self.as_user(office, client_set_value, "Hostel Grievance", name, values)
		self.assertEqual(
			self.stored(name, "assigned_to", "is_confidential", "category"),
			{"assigned_to": None, "is_confidential": 1, "category": "Ragging or Harassment"},
		)
		# The hook judges the stored record, not the request's copy
		doc = frappe.get_doc("Hostel Grievance", name)
		doc.assigned_to, doc.is_confidential = office, 0
		self.assertFalse(grievance_has_permission(doc, "write", office))

	def test_assigned_staff_cannot_declassify(self):
		name = self.raise_grievance(category="Ragging or Harassment")
		frappe.get_doc("Hostel Grievance", name).assign_to_user(self.deputy, self.warden)
		with self.assertRaises(frappe.PermissionError):
			self.as_user(self.deputy, client_set_value, "Hostel Grievance", name, {"category": "Other", "is_confidential": 0})
		self.assertEqual(self.stored(name, "is_confidential").is_confidential, 1)
		# The Warden may correct a misfiled complaint
		self.as_user(self.warden, client_set_value, "Hostel Grievance", name, {"category": "Other", "is_confidential": 0})
		self.assertEqual(self.stored(name, "category").category, "Other")

	def test_staff_may_make_a_grievance_confidential(self):
		name = self.raise_grievance(subject="Seniors keep calling me to their room")
		office = make_user([OFFICE_ROLE])
		self.as_user(office, client_set_value, "Hostel Grievance", name, {"category": "Ragging or Harassment"})
		self.assertEqual(self.stored(name, "is_confidential", "priority"), {"is_confidential": 1, "priority": "Urgent"})
		doc = frappe.get_doc("Hostel Grievance", name)
		self.assertFalse(frappe.has_permission("Hostel Grievance", "read", doc=doc, user=office))

	def test_office_can_register_a_ragging_complaint_for_a_student(self):
		office = make_user([OFFICE_ROLE])
		saved = self.as_user(
			office,
			client_insert,
			{
				"doctype": "Hostel Grievance",
				"student": self.student.name,
				"category": "Ragging or Harassment",
				"subject": "Reported at the office",
				"description": "Student came to the office in person.",
				"priority": "Low",
				"is_confidential": 1,
			},
		)
		self.assertEqual((saved["status"], saved["is_confidential"]), ("Escalated", 1))
		doc = frappe.get_doc("Hostel Grievance", saved["name"])
		self.assertFalse(frappe.has_permission("Hostel Grievance", "read", doc=doc, user=office))

	def test_an_ordinary_save_cannot_handle_a_grievance(self):
		name = self.raise_grievance()
		office = make_user([OFFICE_ROLE])
		for values in (
			{"status": "Closed", "resolution": "fake"},
			{"assigned_to": office},
			{"escalation_level": 0, "sla_due_by": "2099-01-01 00:00:00"},
		):
			with self.assertRaises(frappe.PermissionError):
				self.as_user(office, client_set_value, "Hostel Grievance", name, values)
		self.assertEqual(self.stored(name, "status", "assigned_to"), {"status": "Open", "assigned_to": None})
		# Correcting the category or priority is still an ordinary edit
		self.as_user(office, client_set_value, "Hostel Grievance", name, {"priority": "High"})
		self.assertEqual(self.stored(name, "priority").priority, "High")

	def test_a_new_grievance_has_no_handling_history(self):
		doc = frappe.get_doc(
			{
				"doctype": "Hostel Grievance",
				"student": self.student.name,
				"category": "Water",
				"subject": "No water",
				"description": "Since morning",
				"priority": "Medium",
				"status": "Resolved",
				"assigned_to": self.warden,
				"resolution": "Done",
				"updates": [{"message": "Fixed long ago", "update_by": self.warden}],
			}
		).insert()
		self.assertEqual(doc.status, "Open")
		self.assertFalse(doc.assigned_to)
		self.assertFalse(doc.resolution)
		self.assertEqual([row.message for row in doc.updates], ["Grievance registered."])

	def test_scoped_staff_cannot_move_a_grievance_into_their_hostel(self):
		other = make_student(make_hostel())
		name = self.raise_grievance(student=other)
		office = make_user([OFFICE_ROLE])
		scope_to_hostel(office, self.hostel)
		for hostel in (self.hostel, ""):
			with self.assertRaises(frappe.PermissionError):
				self.as_user(office, client_set_value, "Hostel Grievance", name, {"hostel": hostel})
		self.assertEqual(self.stored(name, "hostel").hostel, other.hostel)

	def test_form_actions_work_on_the_stored_grievance(self):
		name = self.raise_grievance(category="Ragging or Harassment")
		office = make_user([OFFICE_ROLE])
		form_copy = frappe.get_doc("Hostel Grievance", name)
		form_copy.assigned_to = office
		with self.assertRaises(frappe.PermissionError):
			self.as_user(office, run_form_action, form_copy, "resolve", resolution="Nothing happened")
		self.assertEqual(self.stored(name, "status").status, "Escalated")

		form_copy = frappe.get_doc("Hostel Grievance", name)
		self.as_user(self.warden, form_copy.resolve, "Senior counselled and warned")
		self.assertEqual(form_copy.status, "Resolved")

	# ------------------------------------------------------------------ assignees

	def test_auditors_neither_handle_nor_appear_as_assignees(self):
		name = self.raise_grievance()
		doc = frappe.get_doc("Hostel Grievance", name)
		auditor = make_user([AUDITOR_ROLE])
		with self.assertRaises(frappe.ValidationError):
			doc.assign_to_user(auditor, self.warden)

		picker = {row[0] for row in self.as_user(self.warden, staff_user_query, "User", "", "name", 0, 500, {})}
		self.assertIn(self.deputy, picker)
		self.assertNotIn(auditor, picker)

		# One assigned before this rule cannot act on it either
		frappe.db.set_value("Hostel Grievance", name, "assigned_to", auditor)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Hostel Grievance", name).resolve_by("Done", auditor)

	def test_only_grievance_handlers_can_list_staff(self):
		guardian = make_guardian(self.student.name)
		for user in (self.student.user, guardian.user, make_user([AUDITOR_ROLE])):
			with self.assertRaises(frappe.PermissionError):
				self.as_user(user, staff_user_query, "User", "", "name", 0, 20, {})
		mess_manager = make_user([MESS_MANAGER_ROLE])
		picker = {row[0] for row in self.as_user(mess_manager, staff_user_query, "User", "", "name", 0, 500, {})}
		self.assertIn(self.warden, picker)

	# ------------------------------------------------------------------ vacated students

	def test_vacated_students_grievance_stays_with_their_last_hostel(self):
		if not frappe.get_meta("Hostel Student").has_field("last_hostel"):
			self.skipTest("Hostel Student has no last_hostel on this site")
		vacated = make_student(None, status="Vacated")
		frappe.db.set_value("Hostel Student", vacated.name, "last_hostel", self.hostel, update_modified=False)
		name = self.raise_grievance(student=vacated, subject="Caution deposit not refunded")
		self.assertEqual(self.stored(name, "hostel").hostel, self.hostel)

		elsewhere = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(elsewhere, make_hostel())
		self.assertEqual(self.visible_to(elsewhere, [name]), set())
		self.assertEqual(self.visible_to(self.deputy, [name]), {name})

