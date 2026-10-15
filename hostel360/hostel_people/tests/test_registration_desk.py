# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Hostel-scoped registration staff and the people other hostels look after.

Siblings in a boys' and a girls' hostel share a parent, and students come back next term
to a different hostel. Both must work at the desk of the hostel they come to, without
opening up the other hostel's records.
"""

import frappe
from frappe.client import validate_link_and_fetch
from frappe.desk.search import search_link
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from hostel360.hostel_people.page.room_board import room_board
from hostel360.hostel_people.queries import allottable_student_query, guardian_link_query
from hostel360.hostel_people.testing import (
	allot,
	beds_of,
	make_guardian,
	make_hostel,
	make_room,
	make_staff,
	make_student,
	random_mobile,
	unique_code,
)

GUARDIAN_QUERY = "hostel360.hostel_people.queries.guardian_link_query"
STUDENT_QUERY = "hostel360.hostel_people.queries.allottable_student_query"
NEW_ALLOTMENT_FILTERS = {"status": ["in", ["Active", "Applied"]], "current_allotment": ["is", "not set"]}


def link(student: str, guardian: str, relation: str = "Father") -> None:
	doc = frappe.get_doc("Hostel Student", student)
	doc.append("guardians", {"guardian": guardian, "relation": relation})
	doc.save()


def found(rows) -> list[str]:
	return [row[0] for row in rows]


class TestSiblingGuardian(IntegrationTestCase):
	def setUp(self):
		self.boys = make_hostel("Male").name
		self.girls = make_hostel("Female").name
		self.sister = make_student("Female").name
		allot(self.sister, beds_of(make_room(self.girls, capacity=1).name)[0])
		self.mobile = random_mobile()
		self.parent = make_guardian(guardian_name="Meenakshi Sundaram", mobile_number=self.mobile).name
		link(self.sister, self.parent)
		self.office = make_staff("Office Staff", [self.boys]).user

	def test_parent_of_another_hostels_student_is_found_by_exact_mobile(self):
		with self.set_user(self.office):
			self.assertFalse(frappe.has_permission("Hostel Guardian", "read", doc=self.parent))
			self.assertEqual(search_link("Hostel Guardian", self.mobile), [])

			rows = guardian_link_query("Hostel Guardian", self.mobile, "name", 0, 20, {})
			self.assertIn(self.parent, found(rows))
			row = next(row for row in rows if row[0] == self.parent)
			self.assertIn("Meenakshi Sundaram", row)
			self.assertNotIn(self.mobile, " ".join(str(part) for part in row))

			# Typing part of a number, or a name, never lists another hostel's parents.
			self.assertNotIn(
				self.parent, found(guardian_link_query("Hostel Guardian", self.mobile[:7], "name", 0, 20, {}))
			)
			self.assertNotIn(
				self.parent, found(guardian_link_query("Hostel Guardian", "Meenakshi", "name", 0, 20, {}))
			)

			# The exact ID is accepted (Desk validates the chosen value with it) but says nothing more.
			self.assertEqual(
				guardian_link_query("Hostel Guardian", self.parent, "name", 0, 20, {}), [(self.parent,)]
			)

	def test_desk_accepts_the_chosen_guardian_without_revealing_details(self):
		with self.set_user(self.office):
			self.assertEqual(validate_link_and_fetch("Hostel Guardian", self.parent, ["guardian_name"]), {})
			checked = validate_link_and_fetch(
				"Hostel Guardian", self.parent, ["guardian_name", "mobile_number"], query=GUARDIAN_QUERY
			)
			self.assertEqual(checked, {"name": self.parent})

	def test_brother_is_registered_with_the_shared_parent(self):
		with self.set_user(self.office):
			brother = frappe.get_doc(
				{
					"doctype": "Hostel Student",
					"register_number": unique_code("SB", 6),
					"first_name": "Younger",
					"last_name": "Brother",
					"gender": "Male",
					"mobile_number": random_mobile(),
					"guardians": [{"guardian": self.parent, "relation": "Father"}],
				}
			).insert()
			self.assertEqual(brother.guardians[0].guardian_name, "Meenakshi Sundaram")
			# Visible through the new ward now, but still not editable from one hostel alone.
			self.assertTrue(frappe.has_permission("Hostel Guardian", "read", doc=self.parent))
			self.assertFalse(frappe.has_permission("Hostel Guardian", "write", doc=self.parent))

	def test_duplicate_mobile_message_does_not_name_a_hidden_guardian(self):
		with self.set_user(self.office):
			with self.assertRaises(frappe.UniqueValidationError) as hidden:
				make_guardian(mobile_number=self.mobile)
		self.assertNotIn(self.parent, str(hidden.exception))
		self.assertNotIn("Meenakshi", str(hidden.exception))
		self.assertIn("Guardians table", str(hidden.exception))

		with self.assertRaises(frappe.UniqueValidationError) as visible:
			make_guardian(mobile_number=self.mobile)
		self.assertIn(self.parent, str(visible.exception))

	def test_duplicate_student_mobile_message_does_not_name_a_hidden_student(self):
		mobile = frappe.db.get_value("Hostel Student", self.sister, "mobile_number")
		with self.set_user(self.office), self.assertRaises(frappe.UniqueValidationError) as hidden:
			make_student("Male", mobile_number=mobile)
		self.assertNotIn(self.sister, str(hidden.exception))


class TestReturningStudent(IntegrationTestCase):
	def setUp(self):
		self.h1 = make_hostel("Male").name
		self.h2 = make_hostel("Male").name
		self.bed1, self.bed1b = beds_of(make_room(self.h1, capacity=2).name)
		self.bed2, self.bed2b = beds_of(make_room(self.h2, capacity=2).name)
		self.office_h1 = make_staff("Office Staff", [self.h1]).user
		self.deputy_h1 = make_staff("Deputy Warden", [self.h1]).user
		self.deputy_h2 = make_staff("Deputy Warden", [self.h2]).user

		self.mobile = random_mobile()
		self.returning = make_student("Male", mobile_number=self.mobile).name
		first = allot(self.returning, self.bed2, from_date=add_days(nowdate(), -30))
		first.reload()
		first.vacate_allotment(add_days(nowdate(), -1), "End of term")

	def residence(self) -> tuple:
		return tuple(frappe.db.get_value("Hostel Student", self.returning, ["current_hostel", "last_hostel"]))

	def test_found_by_exact_register_or_mobile_number_only(self):
		self.assertEqual(self.residence(), (None, self.h2))
		with self.set_user(self.office_h1):
			self.assertFalse(frappe.has_permission("Hostel Student", "read", doc=self.returning))
			self.assertEqual(search_link("Hostel Student", self.returning), [])
			for txt in (self.returning, self.returning.lower(), self.mobile):
				rows = allottable_student_query(
					"Hostel Student", txt, "name", 0, 20, dict(NEW_ALLOTMENT_FILTERS)
				)
				self.assertIn(self.returning, found(rows), txt)
			partial = allottable_student_query(
				"Hostel Student", self.returning[:-2], "name", 0, 20, dict(NEW_ALLOTMENT_FILTERS)
			)
			self.assertNotIn(self.returning, found(partial))
			# A transfer needs a student who holds a bed now.
			transfer = allottable_student_query(
				"Hostel Student", self.returning, "name", 0, 20, {"current_allotment": ["is", "set"]}
			)
			self.assertNotIn(self.returning, found(transfer))
			# The room board passes the hostel's gender.
			girls_only = allottable_student_query(
				"Hostel Student", self.returning, "name", 0, 20, {"gender": "Female"}
			)
			self.assertNotIn(self.returning, found(girls_only))

			checked = validate_link_and_fetch(
				"Hostel Student",
				self.returning,
				["full_name"],
				query=STUDENT_QUERY,
				filters=dict(NEW_ALLOTMENT_FILTERS),
			)
			self.assertEqual(checked.get("name"), self.returning)

	def test_allotted_from_the_room_board_by_another_hostels_office(self):
		with self.set_user(self.office_h1):
			name = room_board.allot(bed=self.bed1, student=self.returning)
		self.assertEqual(frappe.db.get_value("Room Allotment", name, ["docstatus", "status"]), (1, "Active"))
		self.assertEqual(self.residence(), (self.h1, self.h1))
		with self.set_user(self.office_h1):
			self.assertTrue(frappe.has_permission("Hostel Student", "write", doc=self.returning))
		with self.set_user(self.deputy_h2):
			self.assertFalse(frappe.has_permission("Hostel Student", "read", doc=self.returning))

	def test_allotted_through_the_allotment_form_by_a_deputy_warden(self):
		with self.set_user(self.deputy_h1):
			doc = frappe.get_doc(
				{
					"doctype": "Room Allotment",
					"student": self.returning,
					"bed": self.bed1b,
					"allotment_type": "New",
					"from_date": nowdate(),
				}
			).insert()
			doc.submit()
		self.assertEqual(self.residence(), (self.h1, self.h1))

	def test_only_into_beds_the_user_covers(self):
		with self.set_user(self.office_h1):
			doc = frappe.get_doc(
				{
					"doctype": "Room Allotment",
					"student": self.returning,
					"bed": self.bed2b,
					"from_date": nowdate(),
				}
			)
			self.assertRaises(frappe.PermissionError, doc.insert)
		self.assertEqual(self.residence(), (None, self.h2))

	def test_residents_of_another_hostel_stay_out_of_reach(self):
		resident = make_student("Male").name
		allot(resident, self.bed2b)
		with self.set_user(self.office_h1):
			self.assertNotIn(
				resident,
				found(
					allottable_student_query(
						"Hostel Student", resident, "name", 0, 20, dict(NEW_ALLOTMENT_FILTERS)
					)
				),
			)
			self.assertRaises(frappe.PermissionError, room_board.allot, bed=self.bed1, student=resident)
		self.assertEqual(frappe.db.get_value("Hostel Student", resident, "current_hostel"), self.h2)
