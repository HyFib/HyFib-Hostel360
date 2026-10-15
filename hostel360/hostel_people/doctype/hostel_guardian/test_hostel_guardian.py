# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.testing import make_guardian, make_student, random_mobile


class TestHostelGuardian(IntegrationTestCase):
	def test_mobile_is_normalised_and_unique(self):
		mobile = random_mobile()
		guardian = make_guardian(mobile_number=f"91-{mobile}")
		self.assertEqual(guardian.mobile_number, mobile)
		self.assertTrue(guardian.name.startswith("GRD-"))
		with self.assertRaises(frappe.ValidationError):
			make_guardian(mobile_number=mobile)

	def test_a_student_and_guardian_may_share_a_number(self):
		mobile = random_mobile()
		make_student("Male", mobile_number=mobile)
		self.assertEqual(make_guardian(mobile_number=mobile).mobile_number, mobile)

	def test_validated_through_a_validated_ward(self):
		guardian = make_guardian()
		student = make_student("Male", guardians=[{"guardian": guardian.name, "relation": "Father"}])
		guardian.reload()
		guardian.save()
		self.assertEqual(guardian.validation_status, "Pending")

		frappe.db.set_value("Hostel Student", student.name, "validation_status", "Validated")
		guardian.reload()
		guardian.occupation = "Farmer"
		guardian.save()
		self.assertEqual(guardian.validation_status, "Validated")

	def test_name_and_mobile_flow_into_student_rows(self):
		guardian = make_guardian(guardian_name="Old Name")
		student = make_student("Female", guardians=[{"guardian": guardian.name, "relation": "Mother"}])
		new_mobile = random_mobile()
		guardian.reload()
		guardian.guardian_name = "New Name"
		guardian.mobile_number = new_mobile
		guardian.save()
		row = frappe.db.get_value(
			"Hostel Student Guardian", {"parent": student.name}, ["guardian_name", "mobile_number"], as_dict=True
		)
		self.assertEqual((row.guardian_name, row.mobile_number), ("New Name", new_mobile))
		self.assertEqual([w["name"] for w in guardian.get_wards()], [student.name])

	def test_guardian_link_is_indexed(self):
		# Parent sign-in and the parent portal look up a guardian's wards on every request.
		self.assertTrue(frappe.db.has_index("tabHostel Student Guardian", "guardian_index"))
