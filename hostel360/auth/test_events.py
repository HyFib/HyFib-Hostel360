# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import random

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.auth import events
from hostel360.auth.otp import portal_enabled
from hostel360.setup.roles import OFFICE_ROLE, PARENT_ROLE, STUDENT_ROLE


def make_user(*roles: str, user_type: str | None = None) -> str:
	email = f"auth-{frappe.generate_hash(length=8)}@example.com"
	doc = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Auth",
			"last_name": "Tester",
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	)
	if user_type:
		doc.user_type = user_type
	doc.flags.no_welcome_mail = True
	doc.insert(ignore_permissions=True)
	return doc.name


def random_mobile() -> str:
	return random.choice("6789") + "".join(random.choice("0123456789") for _ in range(9))


class TestPortalUserRules(IntegrationTestCase):
	def test_portal_user_cannot_hold_desk_roles(self):
		with self.assertRaises(frappe.ValidationError):
			make_user(STUDENT_ROLE, OFFICE_ROLE)
		with self.assertRaises(frappe.ValidationError):
			make_user(PARENT_ROLE, "System Manager")

	def test_adding_a_desk_role_later_is_refused(self):
		user = frappe.get_doc("User", make_user(STUDENT_ROLE))
		user.append("roles", {"role": OFFICE_ROLE})
		with self.assertRaises(frappe.ValidationError):
			user.save(ignore_permissions=True)

	def test_portal_users_stay_website_users(self):
		user = make_user(STUDENT_ROLE, user_type="System User")
		self.assertEqual(frappe.db.get_value("User", user, "user_type"), "Website User")
		self.assertTrue(events.is_portal_user(user))

	def test_staff_users_are_untouched(self):
		user = make_user(OFFICE_ROLE)
		self.assertEqual(frappe.db.get_value("User", user, "user_type"), "System User")
		self.assertFalse(events.is_portal_user(user))

	def test_home_pages(self):
		self.assertEqual(events.get_website_user_home_page(make_user(STUDENT_ROLE)), "hostel")
		self.assertEqual(events.get_website_user_home_page(make_user(PARENT_ROLE)), "hostel")
		self.assertEqual(events.get_website_user_home_page(make_user(OFFICE_ROLE)), "staff")
		self.assertEqual(events.get_website_user_home_page("Administrator"), "staff")
		self.assertIsNone(events.get_website_user_home_page("Guest"))


class TestPortalLogin(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		if not frappe.db.exists("DocType", "Hostel Student"):
			self.skipTest("Hostel People is not installed on this site")
		self._flags = {
			field: int(portal_enabled(field)) for field in ("student_portal_enabled", "parent_portal_enabled")
		}
		self.addCleanup(self._restore)

	def _restore(self):
		for field, value in self._flags.items():
			frappe.db.set_single_value("Hostel Settings", field, value)

	def make_student(self, user: str | None = None, **state) -> str:
		student = frappe.get_doc(
			{
				"doctype": "Hostel Student",
				"register_number": f"AUTH{frappe.generate_hash(length=8).upper()}",
				"first_name": "Login",
				"gender": "Female",
				"mobile_number": random_mobile(),
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		# Status and validation normally change through Hostel People's own actions.
		frappe.db.set_value(
			"Hostel Student", student.name, {"user": user, "validation_status": "Validated", **state}
		)
		return student.name

	def make_student_user(self, **state) -> str:
		user = make_user(STUDENT_ROLE)
		self.make_student(user, **state)
		return user

	def make_parent_user(self, *ward_states: dict) -> str:
		user = make_user(PARENT_ROLE)
		guardian = frappe.get_doc(
			{
				"doctype": "Hostel Guardian",
				"guardian_name": "Login Parent",
				"relation": "Mother",
				"mobile_number": random_mobile(),
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Hostel Guardian", guardian.name, "user", user)
		for state in ward_states:
			ward = frappe.get_doc("Hostel Student", self.make_student(**state))
			ward.append("guardians", {"guardian": guardian.name, "relation": "Mother"})
			ward.save(ignore_permissions=True)
		return user

	def login(self, user):
		events.on_login(frappe._dict(user=user))

	def test_validated_student_can_sign_in(self):
		self.login(self.make_student_user())

	def test_unvalidated_student_is_refused(self):
		# Tender 3.1: validated against the college register before the first sign-in. A
		# portal user may already exist (bulk import, desk action); it stays closed.
		user = self.make_student_user(validation_status="Pending")
		with self.assertRaises(frappe.AuthenticationError) as refused:
			self.login(user)
		self.assertIn("not been validated", str(refused.exception))

	def test_rejected_or_vacated_student_is_refused(self):
		for values in ({"validation_status": "Rejected"}, {"status": "Vacated"}, {"status": "Alumni"}):
			user = self.make_student_user(**values)
			with self.assertRaises(frappe.AuthenticationError):
				self.login(user)

	def test_parent_needs_a_validated_resident_ward(self):
		# Tender 4.1: the parent portal follows the ward's validation.
		self.login(self.make_parent_user({"validation_status": "Pending"}, {}))
		for wards in (
			({"validation_status": "Pending"},),
			({"status": "Vacated"}, {"validation_status": "Rejected"}),
			(),
		):
			user = self.make_parent_user(*wards)
			with self.assertRaises(frappe.AuthenticationError, msg=wards):
				self.login(user)

	def test_reason_is_available_for_an_existing_session(self):
		user = self.make_student_user(validation_status="Pending")
		self.assertIn("not been validated", events.portal_login_blocked_reason(user))
		self.assertIsNone(events.portal_login_blocked_reason(self.make_student_user()))

	def test_disabled_student_portal_refuses_students(self):
		user = self.make_student_user()
		frappe.db.set_single_value("Hostel Settings", "student_portal_enabled", 0)
		with self.assertRaises(frappe.AuthenticationError):
			self.login(user)

	def test_portal_user_without_hostel_record_is_refused(self):
		with self.assertRaises(frappe.AuthenticationError):
			self.login(make_user(STUDENT_ROLE))
		with self.assertRaises(frappe.AuthenticationError):
			self.login(make_user(PARENT_ROLE))

	def test_staff_login_is_not_affected(self):
		frappe.db.set_single_value("Hostel Settings", "student_portal_enabled", 0)
		self.login(make_user(OFFICE_ROLE))
		self.login("Administrator")

	def test_session_hook_never_raises(self):
		events.on_session_creation(frappe._dict(user=self.make_student_user()))
		events.on_session_creation(frappe._dict(user=None))
