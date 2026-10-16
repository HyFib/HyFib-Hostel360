# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Which hostel a student belongs to, and which staff look after them."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from hostel360.hostel_people import permissions
from hostel360.hostel_people.services import (
	get_scoped_staff_users,
	get_staff_scope,
	get_student_hostel,
	staff_covers,
	staff_covers_student,
)
from hostel360.hostel_people.setup import backfill_last_hostel
from hostel360.hostel_people.testing import (
	allot,
	beds_of,
	make_block,
	make_desk_user,
	make_guardian,
	make_hostel,
	make_room,
	make_staff,
	make_student,
	random_mobile,
)
from hostel360.setup.roles import (
	DEPUTY_WARDEN_ROLE,
	MESS_MANAGER_ROLE,
	OFFICE_ROLE,
	STUDENT_ROLE,
	WARDEN_ROLE,
)

# The hooks hostel360/hooks.py registers for these DocTypes, wired here so the tests run
# the functions through Frappe's own list and record permission checks.
PERMISSION_HOOKS = {
	"permission_query_conditions": {
		"Hostel Student": ["hostel360.hostel_people.permissions.student_query_conditions"],
		"Room Allotment": ["hostel360.hostel_people.permissions.allotment_query_conditions"],
		"Hostel Guardian": ["hostel360.hostel_people.permissions.guardian_query_conditions"],
	},
	"has_permission": {
		"Hostel Student": ["hostel360.hostel_people.permissions.student_has_permission"],
		"Room Allotment": ["hostel360.hostel_people.permissions.allotment_has_permission"],
		"Hostel Guardian": ["hostel360.hostel_people.permissions.guardian_has_permission"],
	},
}


def hostels_of(student: str) -> tuple:
	return tuple(frappe.db.get_value("Hostel Student", student, ["current_hostel", "last_hostel"]))


def vacate(allotment) -> None:
	allotment.reload()
	allotment.vacate_allotment(nowdate(), "Left the hostel")


class TestStudentHostel(IntegrationTestCase):
	def setUp(self):
		self.h1 = make_hostel("Male").name
		self.h2 = make_hostel("Male").name
		self.bed1 = beds_of(make_room(self.h1, capacity=1).name)[0]
		self.bed2 = beds_of(make_room(self.h2, capacity=1).name)[0]

	def test_last_hostel_is_kept_on_vacate_and_restored_on_cancel(self):
		student = make_student("Male").name
		self.assertIsNone(get_student_hostel(student))

		first = allot(student, self.bed1, from_date=add_days(nowdate(), -10))
		self.assertEqual(hostels_of(student), (self.h1, self.h1))

		vacate(first)
		self.assertEqual(hostels_of(student), (None, self.h1))
		self.assertEqual(get_student_hostel(student), self.h1)

		second = allot(student, self.bed2)
		self.assertEqual(hostels_of(student), (self.h2, self.h2))
		self.assertEqual(get_student_hostel(student), self.h2)

		# A cancelled allotment never happened: the student belongs to the earlier hostel again.
		second.reload()
		second.cancel()
		self.assertEqual(hostels_of(student), (None, self.h1))

	def test_cancelling_the_only_allotment_clears_last_hostel(self):
		student = make_student("Male").name
		allotment = allot(student, self.bed1)
		allotment.cancel()
		self.assertEqual(hostels_of(student), (None, None))
		self.assertIsNone(get_student_hostel(student))

	def test_transfer_moves_last_hostel(self):
		student = make_student("Male").name
		allotment = allot(student, self.bed1, from_date=add_days(nowdate(), -5))
		allotment.create_transfer(self.bed2, nowdate())
		self.assertEqual(hostels_of(student), (self.h2, self.h2))

	def test_migrated_history_sets_last_hostel(self):
		student = make_student("Male").name
		frappe.flags.hostel360_migration = True
		try:
			allot(
				student,
				self.bed1,
				from_date=add_days(nowdate(), -400),
				to_date=add_days(nowdate(), -100),
				status="Vacated",
			)
		finally:
			frappe.flags.hostel360_migration = False
		self.assertEqual(hostels_of(student), (None, self.h1))

	def test_student_register_keeps_former_residents_under_their_hostel(self):
		from hostel360.hostel_people.report.student_register.student_register import execute

		former = make_student("Male").name
		vacate(allot(former, self.bed1, from_date=add_days(nowdate(), -3)))
		_columns, data, *_rest = execute({"hostel": self.h1, "status": ""})
		row = next(row for row in data if row["name"] == former)
		self.assertEqual(row["last_hostel"], self.h1)
		self.assertFalse(row["current_hostel"])

	def test_backfill_is_idempotent(self):
		former = make_student("Male").name
		vacate(allot(former, self.bed1, from_date=add_days(nowdate(), -3)))
		resident = make_student("Male").name
		allot(resident, self.bed2)
		# Records from before the field existed, and a stale value.
		frappe.db.set_value("Hostel Student", former, "last_hostel", None, update_modified=False)
		frappe.db.set_value("Hostel Student", resident, "last_hostel", self.h1, update_modified=False)

		self.assertGreaterEqual(backfill_last_hostel(), 2)
		self.assertEqual(hostels_of(former), (None, self.h1))
		self.assertEqual(hostels_of(resident), (self.h2, self.h2))
		self.assertEqual(backfill_last_hostel(), 0)


class TestStaffScope(IntegrationTestCase):
	def setUp(self):
		self.h1 = make_hostel("Male").name
		self.h2 = make_hostel("Male").name
		self.block_a = make_block(self.h1, "A").name
		self.block_b = make_block(self.h1, "B").name
		self.room_a = make_room(self.h1, capacity=2, block=self.block_a).name
		self.room_b = make_room(self.h1, capacity=2, block=self.block_b).name
		self.room_h2 = make_room(self.h2, capacity=2).name

		self.deputy_whole = make_staff("Deputy Warden", [self.h1]).user
		self.deputy_a = make_staff("Deputy Warden", [{"hostel": self.h1, "block": self.block_a}]).user
		self.deputy_h2 = make_staff("Deputy Warden", [self.h2]).user
		self.warden = make_staff("Warden", []).user
		self.mess_manager = make_desk_user(MESS_MANAGER_ROLE)
		self.office_unassigned = make_desk_user(OFFICE_ROLE)

		self.student_a = make_student("Male").name
		self.allotment_a = allot(self.student_a, beds_of(self.room_a)[0])
		self.student_b = make_student("Male").name
		self.allotment_b = allot(self.student_b, beds_of(self.room_b)[0])

	def test_scope_shapes(self):
		self.assertIsNone(get_staff_scope(self.warden))
		self.assertIsNone(get_staff_scope("Administrator"))
		self.assertIsNone(get_staff_scope(self.mess_manager))
		self.assertEqual(get_staff_scope(self.deputy_whole), {self.h1: None})
		self.assertEqual(get_staff_scope(self.deputy_a), {self.h1: frozenset({self.block_a})})
		# A hostel-scoped role without a hostel must not mean "everywhere".
		self.assertEqual(get_staff_scope(self.office_unassigned), {})
		self.assertEqual(get_staff_scope(make_desk_user(STUDENT_ROLE)), {})

	def test_scoped_staff_users_follow_blocks(self):
		in_a = get_scoped_staff_users(DEPUTY_WARDEN_ROLE, self.h1, self.block_a)
		self.assertIn(self.deputy_whole, in_a)
		self.assertIn(self.deputy_a, in_a)
		self.assertNotIn(self.deputy_h2, in_a)

		in_b = get_scoped_staff_users(DEPUTY_WARDEN_ROLE, self.h1, self.block_b)
		self.assertIn(self.deputy_whole, in_b)
		self.assertNotIn(self.deputy_a, in_b)

		whole_hostel = get_scoped_staff_users(DEPUTY_WARDEN_ROLE, self.h1)
		self.assertIn(self.deputy_whole, whole_hostel)
		self.assertNotIn(self.deputy_a, whole_hostel)

		self.assertIn(self.deputy_h2, get_scoped_staff_users(DEPUTY_WARDEN_ROLE, self.h2))
		self.assertIn(self.warden, get_scoped_staff_users(WARDEN_ROLE, self.h2))
		self.assertIn(self.mess_manager, get_scoped_staff_users(MESS_MANAGER_ROLE, self.h1))
		self.assertNotIn(self.office_unassigned, get_scoped_staff_users(OFFICE_ROLE, self.h1))
		self.assertEqual(get_scoped_staff_users("", self.h1), [])

	def test_inactive_staff_are_not_recipients(self):
		staff = frappe.get_doc("Hostel Staff", {"user": self.deputy_whole})
		staff.is_active = 0
		staff.save()
		self.assertNotIn(self.deputy_whole, get_scoped_staff_users(DEPUTY_WARDEN_ROLE, self.h1))

	def test_staff_covers_student(self):
		self.assertTrue(staff_covers_student(self.deputy_whole, self.student_a))
		self.assertTrue(staff_covers_student(self.deputy_whole, self.student_b))
		self.assertTrue(staff_covers_student(self.deputy_a, self.student_a))
		self.assertFalse(staff_covers_student(self.deputy_a, self.student_b))
		self.assertFalse(staff_covers_student(self.deputy_h2, self.student_a))
		self.assertTrue(staff_covers_student(self.warden, self.student_b))
		self.assertTrue(staff_covers_student(self.mess_manager, self.student_b))
		self.assertFalse(staff_covers_student(self.office_unassigned, self.student_a))
		self.assertFalse(staff_covers_student(make_desk_user(STUDENT_ROLE), self.student_a))

		# A former resident counts under their last hostel, but no longer lives in a block.
		vacate(self.allotment_a)
		self.assertTrue(staff_covers_student(self.deputy_whole, self.student_a))
		self.assertFalse(staff_covers_student(self.deputy_a, self.student_a))
		self.assertFalse(staff_covers_student(self.deputy_h2, self.student_a))

		never_allotted = make_student("Male").name
		self.assertFalse(staff_covers_student(self.deputy_whole, never_allotted))
		self.assertTrue(staff_covers_student(self.warden, never_allotted))

		self.assertTrue(staff_covers(self.deputy_a, self.h1, self.block_a))
		self.assertFalse(staff_covers(self.deputy_a, self.h1, self.block_b))
		self.assertFalse(staff_covers(self.deputy_a, self.h1))
		self.assertTrue(staff_covers(self.deputy_whole, self.h1))

	def test_block_scoped_desk_access(self):
		unallotted = make_student("Male").name
		names = (self.student_a, self.student_b, unallotted)

		def visible_students(user):
			condition = permissions.student_query_conditions(user) or "1=1"
			return set(
				frappe.db.sql_list(
					f"select name from `tabHostel Student` where name in %(names)s and {condition}",  # noqa: S608 - condition comes from the permission hook under test
					{"names": names},
				)
			)

		self.assertEqual(visible_students(self.deputy_a), {self.student_a, unallotted})
		self.assertEqual(visible_students(self.deputy_whole), set(names))
		self.assertEqual(permissions.student_query_conditions(self.warden), "")

		doc_a = frappe.get_doc("Hostel Student", self.student_a)
		doc_b = frappe.get_doc("Hostel Student", self.student_b)
		self.assertTrue(permissions.student_has_permission(doc_a, "read", self.deputy_a))
		self.assertFalse(permissions.student_has_permission(doc_b, "write", self.deputy_a))
		self.assertTrue(permissions.student_has_permission(doc_b, "write", self.deputy_whole))

		# A request cannot move the student into the user's block to reach the record.
		doc_b.current_room = self.room_a
		self.assertFalse(permissions.student_has_permission(doc_b, "write", self.deputy_a))

		allotments = (self.allotment_a.name, self.allotment_b.name)
		condition = permissions.allotment_query_conditions(self.deputy_a)
		self.assertEqual(
			set(
				frappe.db.sql_list(
					f"select name from `tabRoom Allotment` where name in %(names)s and {condition}",  # noqa: S608 - condition comes from the permission hook under test
					{"names": allotments},
				)
			),
			{self.allotment_a.name},
		)
		self.assertTrue(permissions.allotment_has_permission(self.allotment_a, "submit", self.deputy_a))
		self.assertFalse(permissions.allotment_has_permission(self.allotment_b, "submit", self.deputy_a))
		self.assertTrue(permissions.allotment_has_permission(self.allotment_b, "submit", self.deputy_whole))

	def test_block_scope_through_frappe_permission_checks(self):
		students = [self.student_a, self.student_b]
		allotments = [self.allotment_a.name, self.allotment_b.name]
		with self.patch_hooks(PERMISSION_HOOKS):
			with self.set_user(self.deputy_a):
				self.assertEqual(
					frappe.get_list("Hostel Student", filters={"name": ["in", students]}, pluck="name"),
					[self.student_a],
				)
				self.assertEqual(
					frappe.get_list("Room Allotment", filters={"name": ["in", allotments]}, pluck="name"),
					[self.allotment_a.name],
				)
				self.assertTrue(frappe.has_permission("Hostel Student", "write", doc=self.student_a))
				self.assertFalse(frappe.has_permission("Hostel Student", "read", doc=self.student_b))
				self.assertFalse(frappe.has_permission("Room Allotment", "submit", doc=self.allotment_b.name))
			with self.set_user(self.deputy_whole):
				self.assertEqual(
					set(frappe.get_list("Hostel Student", filters={"name": ["in", students]}, pluck="name")),
					set(students),
				)

	def test_stored_hostel_decides_access_to_existing_records(self):
		doc = frappe.get_doc("Hostel Student", self.student_a)
		doc.current_hostel = self.h2
		doc.last_hostel = self.h2
		self.assertFalse(permissions.student_has_permission(doc, "write", self.deputy_h2))

		allotment = frappe.get_doc("Room Allotment", self.allotment_a.name)
		allotment.hostel = self.h2
		self.assertFalse(permissions.allotment_has_permission(allotment, "write", self.deputy_h2))


class TestHostelDeskScoping(IntegrationTestCase):
	def setUp(self):
		self.h1 = make_hostel("Male").name
		self.h2 = make_hostel("Male").name
		self.bed1, self.bed1b = beds_of(make_room(self.h1, capacity=2).name)
		self.bed2, self.bed2b = beds_of(make_room(self.h2, capacity=2).name)
		self.deputy = make_staff("Deputy Warden", [self.h1]).user

	def test_former_residents_stay_with_their_hostel(self):
		left_h1 = make_student("Male").name
		vacate(allot(left_h1, self.bed1, from_date=add_days(nowdate(), -3)))
		left_h2 = make_student("Male").name
		vacate(allot(left_h2, self.bed2, from_date=add_days(nowdate(), -3)))

		with self.set_user(self.deputy):
			self.assertTrue(frappe.has_permission("Hostel Student", "read", doc=left_h1))
			self.assertFalse(frappe.has_permission("Hostel Student", "read", doc=left_h2))
			listed = set(
				frappe.get_list("Hostel Student", filters={"name": ["in", [left_h1, left_h2]]}, pluck="name")
			)
		self.assertEqual(listed, {left_h1})

	def test_guardians_are_scoped_through_their_wards(self):
		in_h1 = make_student("Male").name
		allot(in_h1, self.bed1)
		in_h2 = make_student("Male").name
		allot(in_h2, self.bed2)
		sibling_h2 = make_student("Male").name
		allot(sibling_h2, self.bed2b)

		own = make_guardian().name
		foreign = make_guardian().name
		shared = make_guardian().name
		unlinked = make_guardian().name
		link(in_h1, own, shared)
		link(in_h2, foreign)
		link(sibling_h2, shared)

		condition = permissions.guardian_query_conditions(self.deputy)
		visible = set(
			frappe.db.sql_list(
				f"select name from `tabHostel Guardian` where name in %(names)s and {condition}",  # noqa: S608 - condition comes from the permission hook under test
				{"names": (own, foreign, shared, unlinked)},
			)
		)
		self.assertEqual(visible, {own, shared, unlinked})

		def allowed(guardian, ptype):
			doc = frappe.get_doc("Hostel Guardian", guardian)
			return permissions.guardian_has_permission(doc, ptype, self.deputy)

		self.assertTrue(allowed(own, "write"))
		self.assertFalse(allowed(foreign, "read"))
		self.assertFalse(allowed(foreign, "write"))
		# A parent of children in two hostels: visible to both, editable by neither alone.
		self.assertTrue(allowed(shared, "read"))
		self.assertFalse(allowed(shared, "write"))
		self.assertTrue(allowed(unlinked, "write"))

		with self.patch_hooks(PERMISSION_HOOKS), self.set_user(self.deputy):
			listed = frappe.get_list(
				"Hostel Guardian", filters={"name": ["in", [own, foreign, shared, unlinked]]}, pluck="name"
			)
			self.assertEqual(set(listed), {own, shared, unlinked})
			self.assertFalse(frappe.has_permission("Hostel Guardian", "read", doc=foreign))
			guardian = frappe.get_doc("Hostel Guardian", shared)
			guardian.mobile_number = random_mobile()
			self.assertRaises(frappe.PermissionError, guardian.save)

		warden = make_staff("Warden", []).user
		self.assertEqual(permissions.guardian_query_conditions(warden), "")
		self.assertTrue(
			permissions.guardian_has_permission(frappe.get_doc("Hostel Guardian", foreign), "write", warden)
		)


def link(student: str, *guardians: str) -> None:
	doc = frappe.get_doc("Hostel Student", student)
	for guardian in guardians:
		doc.append("guardians", {"guardian": guardian, "relation": "Father"})
	doc.save()
