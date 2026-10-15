# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.services import (
	PLACEHOLDER_EMAIL_DOMAIN,
	confirm_profile,
	ensure_portal_user,
	get_student_summary,
	record_privacy_consent,
)
from hostel360.hostel_people.testing import allot, beds_of, make_guardian, make_hostel, make_room, make_student
from hostel360.setup.roles import PARENT_ROLE, STUDENT_ROLE


class TestPortalUsers(IntegrationTestCase):
	def test_student_portal_user_is_idempotent(self):
		student = make_student("Male")
		user = ensure_portal_user("Hostel Student", student.name)
		self.assertEqual(ensure_portal_user("Hostel Student", student.name), user)
		self.assertEqual(frappe.db.count("User", {"name": user}), 1)

		user_doc = frappe.get_doc("User", user)
		self.assertEqual(user_doc.user_type, "Website User")
		self.assertIn(STUDENT_ROLE, frappe.get_roles(user))
		self.assertFalse(user_doc.mobile_no)
		self.assertTrue(user.endswith(f"@{PLACEHOLDER_EMAIL_DOMAIN}"))
		self.assertEqual(frappe.db.get_value("Hostel Student", student.name, "user"), user)
		has_password = frappe.db.sql(
			"select count(*) from `__Auth` where doctype = 'User' and name = %s and fieldname = 'password'", user
		)[0][0]
		self.assertTrue(has_password)

	def test_real_email_is_used_when_free(self):
		email = f"student-{frappe.generate_hash(length=8)}@example.com"
		student = make_student("Female", email=email)
		self.assertEqual(ensure_portal_user("Hostel Student", student.name), email)

		# The guardian shares the e-mail, so gets a placeholder of their own.
		guardian = make_guardian(email=email)
		guardian_user = ensure_portal_user("Hostel Guardian", guardian.name)
		self.assertNotEqual(guardian_user, email)
		self.assertIn(PARENT_ROLE, frappe.get_roles(guardian_user))
		self.assertEqual(ensure_portal_user("Hostel Guardian", guardian.name), guardian_user)

	def test_staff_account_is_never_reused(self):
		email = f"warden-{frappe.generate_hash(length=8)}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Warden",
				"send_welcome_email": 0,
				"roles": [{"role": "Hostel Warden"}],
			}
		).insert()
		student = make_student("Male", email=email)
		user = ensure_portal_user("Hostel Student", student.name)
		self.assertNotEqual(user, email)
		self.assertNotIn(STUDENT_ROLE, frappe.get_roles(email))

	def test_shared_mobile_gives_two_identities(self):
		student = make_student("Male")
		guardian = make_guardian(mobile_number=student.mobile_number)
		self.assertNotEqual(
			ensure_portal_user("Hostel Student", student.name), ensure_portal_user("Hostel Guardian", guardian.name)
		)


class TestStudentServices(IntegrationTestCase):
	def test_summary_masks_mobile_numbers(self):
		hostel = make_hostel("Male")
		room = make_room(hostel.name, capacity=2)
		guardian = make_guardian()
		student = make_student("Male", guardians=[{"guardian": guardian.name, "relation": "Father"}])
		allot(student.name, beds_of(room.name)[0])

		summary = get_student_summary(student.name)
		self.assertNotIn(student.mobile_number, frappe.as_json(summary))
		self.assertNotIn(guardian.mobile_number, frappe.as_json(summary))
		self.assertNotIn("medical_notes", summary)
		self.assertEqual(summary["room"]["hostel"], hostel.name)
		self.assertEqual(summary["room"]["bed_label"], "A")
		self.assertEqual(summary["guardians"][0]["relation"], "Father")

	def test_confirm_profile_applies_only_allowed_fields(self):
		student = make_student("Female", first_name="Meena")
		confirm_profile(
			student.name,
			{
				"email": "Meena.Student@Example.com",
				"city": "Karaikudi",
				"pincode": "630003",
				"blood_group": "O+",
				"first_name": "Changed",
				"mobile_number": "9000000001",
				"validation_status": "Validated",
			},
		)
		student.reload()
		self.assertEqual(student.email, "meena.student@example.com")
		self.assertEqual(student.city, "Karaikudi")
		self.assertEqual(student.blood_group, "O+")
		self.assertEqual(student.first_name, "Meena")
		self.assertNotEqual(student.mobile_number, "9000000001")
		self.assertEqual(student.validation_status, "Pending")
		self.assertEqual(student.profile_confirmed, 1)

		self.assertRaises(frappe.ValidationError, confirm_profile, student.name, {"pincode": "12"})
		self.assertRaises(frappe.ValidationError, confirm_profile, student.name, {"blood_group": "Z"})

	def test_privacy_consent_requires_current_version(self):
		student = make_student("Male")
		version = frappe.db.get_single_value("Hostel Settings", "privacy_notice_version")
		self.assertRaises(frappe.ValidationError, record_privacy_consent, "Hostel Student", student.name, "0.1")
		record_privacy_consent("Hostel Student", student.name, version)
		student.reload()
		self.assertEqual(student.privacy_consent_version, version)
		self.assertTrue(student.privacy_consent_on)

		guardian = make_guardian()
		record_privacy_consent("Hostel Guardian", guardian.name, version)
		self.assertEqual(frappe.db.get_value("Hostel Guardian", guardian.name, "privacy_consent_version"), version)
		self.assertRaises(frappe.ValidationError, record_privacy_consent, "User", "Administrator", version)

	def test_portal_user_cannot_read_another_student(self):
		student = make_student("Male")
		other = make_student("Male")
		user = ensure_portal_user("Hostel Student", student.name)
		frappe.set_user(user)
		try:
			self.assertEqual(get_student_summary(student.name)["name"], student.name)
			self.assertRaises(frappe.PermissionError, get_student_summary, other.name)
			self.assertRaises(frappe.PermissionError, confirm_profile, other.name, {"city": "Madurai"})
		finally:
			frappe.set_user("Administrator")
