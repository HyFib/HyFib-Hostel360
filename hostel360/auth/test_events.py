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
		self.assertEqual(events.get_website_user_home_page(make_user(OFFICE_ROLE)), "desk")
		self.assertEqual(events.get_website_user_home_page("Administrator"), "desk")
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

	def make_student_user(self, **state) -> str:
		user = make_user(STUDENT_ROLE)
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
		frappe.db.set_value("Hostel Student", student.name, {"user": user, **state})
		return user

	def login(self, user):
		events.on_login(frappe._dict(user=user))

	def test_active_student_can_sign_in(self):
		self.login(self.make_student_user())

	def test_pending_student_can_sign_in(self):
		self.login(self.make_student_user(validation_status="Pending"))

	def test_rejected_or_vacated_student_is_refused(self):
		for values in ({"validation_status": "Rejected"}, {"status": "Vacated"}):
			user = self.make_student_user(**values)
			with self.assertRaises(frappe.AuthenticationError):
				self.login(user)

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
