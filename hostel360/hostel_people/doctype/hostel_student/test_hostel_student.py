# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.services import ensure_portal_user
from hostel360.hostel_people.testing import (
	allot,
	beds_of,
	make_desk_user,
	make_guardian,
	make_hostel,
	make_room,
	make_student,
	random_mobile,
	unique_code,
)
from hostel360.setup.roles import MESS_MANAGER_ROLE, OFFICE_ROLE


class TestHostelStudent(IntegrationTestCase):
	def test_mobile_number_is_normalised(self):
		mobile = random_mobile()
		student = make_student("Male", mobile_number=f"+91 {mobile[:5]} {mobile[5:]}")
		self.assertEqual(student.mobile_number, mobile)

		other = make_student("Female", mobile_number=f"0{random_mobile()}")
		self.assertEqual(len(other.mobile_number), 10)

	def test_invalid_mobile_is_rejected(self):
		for value in ("12345", "5123456789", "98765-4321x"):
			with self.assertRaises(frappe.ValidationError):
				make_student("Male", mobile_number=value)

	def test_mobile_must_be_unique_among_students(self):
		mobile = random_mobile()
		make_student("Male", mobile_number=mobile)
		with self.assertRaises(frappe.ValidationError):
			make_student("Male", mobile_number=f"+91{mobile}")

	def test_names_and_register_number(self):
		code = unique_code("rg", 5)
		student = make_student(
			"Male", register_number=f" {code.lower()} ", first_name="  Arun ", last_name=" Kumar  "
		)
		self.assertEqual(student.name, code)
		self.assertEqual(student.full_name, "Arun Kumar")
		self.assertTrue(student.academic_year)

	def test_guardian_rows(self):
		father = make_guardian(guardian_name="Ravi Kumar")
		mother = make_guardian(guardian_name="Lakshmi Ravi", relation="Mother")
		student = make_student(
			"Male",
			guardians=[
				{"guardian": father.name, "relation": "Father"},
				{"guardian": mother.name, "relation": "Mother"},
			],
		)
		self.assertEqual([row.is_primary for row in student.guardians], [1, 0])
		self.assertEqual(student.guardians[0].mobile_number, father.mobile_number)

		student.append("guardians", {"guardian": father.name, "relation": "Father"})
		self.assertRaises(frappe.ValidationError, student.save)

		student.reload()
		student.guardians[1].is_primary = 1
		self.assertRaises(frappe.ValidationError, student.save)

	def test_validation_validates_guardians(self):
		guardian = make_guardian()
		student = make_student("Male", guardians=[{"guardian": guardian.name, "relation": "Father"}])
		self.assertEqual(student.validation_status, "Pending")

		student.mark_validated()
		student.reload()
		self.assertEqual(student.validation_status, "Validated")
		self.assertEqual(student.validated_by, frappe.session.user)
		self.assertTrue(student.validated_on)
		self.assertEqual(
			frappe.db.get_value("Hostel Guardian", guardian.name, "validation_status"), "Validated"
		)

		# A guardian added later to a validated student is validated too.
		late = make_guardian(relation="Mother")
		student.append("guardians", {"guardian": late.name, "relation": "Mother"})
		student.save()
		self.assertEqual(frappe.db.get_value("Hostel Guardian", late.name, "validation_status"), "Validated")

	def test_reject_needs_a_reason(self):
		student = make_student("Female")
		self.assertRaises(frappe.ValidationError, student.mark_rejected, "")
		student.mark_rejected("Register number does not match the admission list")
		student.reload()
		self.assertEqual(student.validation_status, "Rejected")
		self.assertIn("admission list", student.rejection_reason)

	def test_resident_cannot_be_marked_vacated(self):
		hostel = make_hostel("Male")
		bed = beds_of(make_room(hostel.name, capacity=1).name)[0]
		student = make_student("Male")
		allot(student.name, bed)
		student.reload()
		student.status = "Vacated"
		self.assertRaises(frappe.ValidationError, student.save)

		student.reload()
		student.gender = "Female"
		self.assertRaises(frappe.ValidationError, student.save)

	def test_system_fields_ignore_form_and_api_values(self):
		hostel = make_hostel("Male")
		bed = beds_of(make_room(hostel.name, capacity=1).name)[0]
		student = make_student("Male", validation_status="Validated", user="Administrator", current_bed=bed)
		self.assertEqual(student.validation_status, "Pending")
		self.assertIsNone(student.user)
		self.assertIsNone(student.current_bed)

		student.validation_status = "Validated"
		student.current_bed = bed
		student.profile_confirmed = 1
		student.save()
		student.reload()
		self.assertEqual(student.validation_status, "Pending")
		self.assertIsNone(student.current_bed)
		self.assertEqual(student.profile_confirmed, 0)

	def test_validation_ignores_a_tampered_client_copy(self):
		owner = make_student("Male")
		owner_user = ensure_portal_user("Hostel Student", owner.name)
		hostel = make_hostel("Male")
		bed = beds_of(make_room(hostel.name, capacity=1).name)[0]
		target = make_student("Male")

		# What a crafted request sends with the call: the form's copy with protected fields rewritten.
		sent = frappe.get_doc(
			{
				**target.as_dict(),
				"user": owner_user,
				"profile_confirmed": 1,
				"privacy_consent_version": "forged",
				"current_bed": bed,
				"last_hostel": hostel.name,
			}
		)
		sent.mark_validated()

		stored = frappe.db.get_value(
			"Hostel Student",
			target.name,
			[
				"validation_status",
				"user",
				"profile_confirmed",
				"privacy_consent_version",
				"current_bed",
				"last_hostel",
			],
			as_dict=True,
		)
		self.assertEqual(stored.validation_status, "Validated")
		self.assertIsNone(stored.user)
		self.assertEqual(stored.profile_confirmed, 0)
		self.assertIsNone(stored.privacy_consent_version)
		self.assertIsNone(stored.current_bed)
		self.assertIsNone(stored.last_hostel)
		self.assertEqual(
			frappe.get_all("Hostel Student", filters={"user": owner_user}, pluck="name"), [owner.name]
		)

	def test_rejection_checks_the_stored_allotment(self):
		hostel = make_hostel("Male")
		bed = beds_of(make_room(hostel.name, capacity=1).name)[0]
		student = make_student("Male")
		allotment = allot(student.name, bed)

		sent = frappe.get_doc(
			{
				**frappe.get_doc("Hostel Student", student.name).as_dict(),
				"current_allotment": None,
				"current_bed": None,
				"current_room": None,
				"current_hostel": None,
			}
		)
		self.assertRaises(frappe.ValidationError, sent.mark_rejected, "Does not match the admission list")
		stored = frappe.db.get_value(
			"Hostel Student", student.name, ["validation_status", "current_allotment"], as_dict=True
		)
		self.assertEqual(stored.validation_status, "Pending")
		self.assertEqual(stored.current_allotment, allotment.name)

	def test_validation_keeps_a_portal_link_made_after_the_form_opened(self):
		student = make_student("Male")
		form = frappe.get_doc("Hostel Student", student.name)
		# The student signs in for the first time while the office has the form open.
		user = ensure_portal_user("Hostel Student", student.name)
		form.mark_validated()
		self.assertEqual(frappe.db.get_value("Hostel Student", student.name, "user"), user)

	def test_form_offers_unlock_sign_in_to_the_office(self):
		student = make_student("Male")
		offered = {}
		for role in (OFFICE_ROLE, MESS_MANAGER_ROLE):
			with self.set_user(make_desk_user(role)):
				form = frappe.get_doc("Hostel Student", student.name)
				form.run_method("onload")
				offered[role] = form.get_onload().get("can_unlock_sign_in")
		self.assertEqual(offered, {OFFICE_ROLE: True, MESS_MANAGER_ROLE: False})
