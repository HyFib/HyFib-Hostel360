# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.doctype.hostel_staff.hostel_staff import get_designation_rules
from hostel360.hostel_people.services import get_staff_scope
from hostel360.hostel_people.testing import make_block, make_desk_user, make_hostel
from hostel360.setup.roles import (
	ADMIN_ROLE,
	DEPUTY_WARDEN_ROLE,
	MESS_STAFF_ROLE,
	OFFICE_ROLE,
	OPERATIONS_ROLE,
	SECURITY_ROLE,
	STAFF_ROLES,
	WARDEN_ROLE,
)


def make_staff(designation: str, hostels: list[str], **values):
	suffix = frappe.generate_hash(length=8)
	return frappe.get_doc(
		{
			"doctype": "Hostel Staff",
			"staff_name": values.pop("staff_name", f"Test Staff {suffix}"),
			"designation": designation,
			"email": values.pop("email", f"staff-{suffix}@example.com"),
			"create_user": values.pop("create_user", 1),
			"send_welcome_email": 0,
			"is_active": 1,
			"assignments": [{"hostel": hostel} for hostel in hostels],
			**values,
		}
	).insert()


def hostel_permissions(user: str) -> set[str]:
	return set(frappe.get_all("User Permission", filters={"user": user, "allow": "Hostel"}, pluck="for_value"))


def open_session(user: str) -> str:
	"""A signed-in session of the user, as the login flow stores it."""
	sid = frappe.generate_hash()
	frappe.db.sql(
		"insert into `tabSessions` (sid, user, sessiondata, lastupdate, status)"
		" values (%(sid)s, %(user)s, '{}', now(), 'Active')",
		{"sid": sid, "user": user},
	)
	return sid


def session_count(user: str) -> int:
	return frappe.db.sql("select count(*) from `tabSessions` where user = %s", user)[0][0]


class TestHostelStaff(IntegrationTestCase):
	def setUp(self):
		self.hostel_a = make_hostel("Male").name
		self.hostel_b = make_hostel("Female").name

	def test_desk_user_and_hostel_scope(self):
		staff = make_staff("Deputy Warden", [self.hostel_a, self.hostel_b])
		self.assertTrue(staff.user)
		user = frappe.get_doc("User", staff.user)
		self.assertEqual(user.user_type, "System User")
		self.assertIn(DEPUTY_WARDEN_ROLE, frappe.get_roles(user.name))
		self.assertFalse(user.mobile_no)
		self.assertEqual(hostel_permissions(user.name), {self.hostel_a, self.hostel_b})

		# Removing an assignment removes the stale permission; a single hostel becomes the default.
		staff.reload()
		staff.assignments = [row for row in staff.assignments if row.hostel == self.hostel_a]
		staff.save()
		self.assertEqual(hostel_permissions(user.name), {self.hostel_a})
		self.assertEqual(
			frappe.db.get_value("User Permission", {"user": user.name, "allow": "Hostel"}, "is_default"), 1
		)

		# Wardens see every hostel: roles follow the designation and permissions go away.
		staff.reload()
		staff.designation = "Warden"
		staff.save()
		roles = frappe.get_roles(user.name)
		self.assertIn(WARDEN_ROLE, roles)
		self.assertNotIn(DEPUTY_WARDEN_ROLE, roles)
		self.assertEqual(hostel_permissions(user.name), set())

	def test_deactivation_disables_login(self):
		staff = make_staff("Office Staff", [self.hostel_a])
		self.assertIn(OFFICE_ROLE, frappe.get_roles(staff.user))
		staff.reload()
		staff.is_active = 0
		staff.save()
		self.assertEqual(frappe.db.get_value("User", staff.user, "enabled"), 0)
		staff.reload()
		staff.is_active = 1
		staff.save()
		self.assertEqual(frappe.db.get_value("User", staff.user, "enabled"), 1)

	def test_scoped_designation_needs_a_hostel(self):
		with self.assertRaises(frappe.ValidationError):
			make_staff("Mess Staff", [])

	def test_no_login_for_non_desk_designations(self):
		for designation in ("Housekeeping", "Other"):
			with self.assertRaisesRegex(frappe.ValidationError, "contact purposes"):
				make_staff(designation, [self.hostel_a])
			staff = make_staff(designation, [self.hostel_a], create_user=0)
			self.assertFalse(staff.user)

		# Security staff may also be recorded without a login, for example before they have an e-mail.
		staff = make_staff("Security", [self.hostel_a], create_user=0, email=None)
		self.assertFalse(staff.user)

	def test_existing_user_is_reused_and_other_roles_kept(self):
		email = f"existing-{frappe.generate_hash(length=8)}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Existing",
				"send_welcome_email": 0,
				"roles": [{"role": "System Manager"}],
			}
		).insert()
		staff = make_staff("Office Staff", [self.hostel_a], email=email)
		self.assertEqual(staff.user, email)
		roles = frappe.get_roles(email)
		self.assertIn("System Manager", roles)
		self.assertIn(OFFICE_ROLE, roles)

		with self.assertRaises(frappe.ValidationError):
			make_staff("Office Staff", [self.hostel_a], email=email)

	def test_block_must_belong_to_hostel(self):
		block = make_block(self.hostel_b, "Z")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Hostel Staff",
					"staff_name": "Block Mismatch",
					"designation": "Warden",
					"assignments": [{"hostel": self.hostel_a, "block": block.name}],
				}
			).insert()

	def test_linked_user_cannot_be_redirected(self):
		staff = make_staff("Office Staff", [self.hostel_a])
		own_user = staff.user
		staff.reload()
		staff.user = "Administrator"
		staff.is_active = 0
		staff.save()
		self.assertEqual(frappe.db.get_value("Hostel Staff", staff.name, "user"), own_user)
		self.assertEqual(frappe.db.get_value("User", "Administrator", "enabled"), 1)
		self.assertEqual(frappe.db.get_value("User", own_user, "enabled"), 0)

	def test_hostel_admin_cannot_take_over_an_account_with_other_roles(self):
		it_account = make_desk_user("System Manager", OPERATIONS_ROLE)
		hostel_admin = make_staff("Hostel Administrator", []).user

		with self.set_user(hostel_admin):
			with self.assertRaises(frappe.PermissionError):
				make_staff("Mess Staff", [self.hostel_a], email=it_account)

		roles = frappe.get_roles(it_account)
		self.assertIn("System Manager", roles)
		self.assertIn(OPERATIONS_ROLE, roles)
		self.assertNotIn(MESS_STAFF_ROLE, roles)
		self.assertEqual(frappe.db.get_value("User", it_account, "enabled"), 1)
		self.assertEqual(hostel_permissions(it_account), set())

		# A login that only ever held staff roles is the Hostel Administrator's to manage.
		former_staff = make_desk_user(MESS_STAFF_ROLE)
		with self.set_user(hostel_admin):
			staff = make_staff("Office Staff", [self.hostel_a], email=former_staff)
		self.assertEqual(staff.user, former_staff)
		self.assertIn(OFFICE_ROLE, frappe.get_roles(former_staff))
		self.assertNotIn(MESS_STAFF_ROLE, frappe.get_roles(former_staff))

	def test_hostel_admin_cannot_disable_an_account_with_other_roles(self):
		it_account = make_desk_user("System Manager")
		# Linked by someone who may edit the account (here: Administrator).
		staff = make_staff("Office Staff", [self.hostel_a], email=it_account)
		hostel_admin = make_staff("Hostel Administrator", []).user

		with self.set_user(hostel_admin):
			staff.reload()
			staff.is_active = 0
			staff.save()
		self.assertEqual(frappe.db.get_value("User", it_account, "enabled"), 1)
		self.assertNotIn(OFFICE_ROLE, frappe.get_roles(it_account))
		self.assertIn("System Manager", frappe.get_roles(it_account))

		with self.set_user(hostel_admin):
			staff.reload()
			staff.is_active = 1
			staff.save()
		self.assertIn(OFFICE_ROLE, frappe.get_roles(it_account))

		with self.set_user(hostel_admin):
			frappe.delete_doc("Hostel Staff", staff.name)
		self.assertEqual(frappe.db.get_value("User", it_account, "enabled"), 1)
		self.assertNotIn(OFFICE_ROLE, frappe.get_roles(it_account))
		self.assertIn("System Manager", frappe.get_roles(it_account))

	def test_deleting_staff_revokes_roles_scope_and_sessions(self):
		staff = make_staff("Deputy Warden", [self.hostel_a])
		user = staff.user
		open_session(user)
		self.assertEqual(session_count(user), 1)

		frappe.delete_doc("Hostel Staff", staff.name)

		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 0)
		self.assertNotIn(DEPUTY_WARDEN_ROLE, frappe.get_roles(user))
		self.assertEqual(hostel_permissions(user), set())
		self.assertEqual(session_count(user), 0)

	def test_deactivation_signs_the_user_out_everywhere(self):
		staff = make_staff("Office Staff", [self.hostel_a])
		open_session(staff.user)
		open_session(staff.user)
		staff.reload()
		staff.is_active = 0
		staff.save()
		self.assertEqual(session_count(staff.user), 0)

	def test_desk_less_designation_switches_the_login_off(self):
		staff = make_staff("Office Staff", [self.hostel_a])
		staff.reload()
		staff.designation = "Housekeeping"
		staff.save()
		self.assertEqual(frappe.db.get_value("User", staff.user, "enabled"), 0)
		self.assertNotIn(OFFICE_ROLE, frappe.get_roles(staff.user))
		self.assertEqual(hostel_permissions(staff.user), set())

		staff.reload()
		staff.designation = "Office Staff"
		staff.save()
		self.assertEqual(frappe.db.get_value("User", staff.user, "enabled"), 1)
		self.assertIn(OFFICE_ROLE, frappe.get_roles(staff.user))
		self.assertEqual(hostel_permissions(staff.user), {self.hostel_a})

	def test_security_staff_get_a_hostel_scoped_gate_login(self):
		staff = make_staff("Security", [self.hostel_a])
		self.assertTrue(staff.user)
		user = frappe.get_doc("User", staff.user)
		self.assertEqual(user.user_type, "System User")
		self.assertTrue(user.enabled)
		roles = set(frappe.get_roles(staff.user))
		self.assertIn(SECURITY_ROLE, roles)
		# The gate role is not a staff role: no student records, fees or reports.
		self.assertFalse(roles & STAFF_ROLES)
		self.assertTrue(frappe.db.get_value("Role", SECURITY_ROLE, "two_factor_auth"))
		self.assertEqual(hostel_permissions(staff.user), {self.hostel_a})
		self.assertEqual(get_staff_scope(staff.user), {self.hostel_a: None})
		self.assertFalse(frappe.has_permission("Hostel Student", "read", user=staff.user))
		self.assertFalse(frappe.has_permission("Hostel Staff", "read", user=staff.user))

		# A block assignment narrows the gate to the students of that block.
		block = make_block(self.hostel_a, "G")
		staff.reload()
		staff.assignments = []
		staff.append("assignments", {"hostel": self.hostel_a, "block": block.name})
		staff.save()
		self.assertEqual(get_staff_scope(staff.user), {self.hostel_a: frozenset({block.name})})

		rules = get_designation_rules()
		self.assertIn("Security", rules["login"])
		self.assertIn("Security", rules["scoped"])
		self.assertNotIn("Housekeeping", rules["login"])
		self.assertIn("Hostel Administrator", rules["login"])
		self.assertIn("Office Staff", rules["scoped"])
		self.assertNotIn(ADMIN_ROLE, rules["scoped"])

	def test_security_login_needs_a_hostel(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Assign at least one hostel"):
			make_staff("Security", [])

	def test_moving_to_and_from_security_swaps_the_role(self):
		staff = make_staff("Office Staff", [self.hostel_a])
		open_session(staff.user)
		staff.reload()
		staff.designation = "Security"
		staff.save()
		roles = frappe.get_roles(staff.user)
		self.assertIn(SECURITY_ROLE, roles)
		self.assertNotIn(OFFICE_ROLE, roles)
		self.assertEqual(frappe.db.get_value("User", staff.user, "enabled"), 1)
		self.assertEqual(session_count(staff.user), 1)
		self.assertEqual(hostel_permissions(staff.user), {self.hostel_a})

		staff.reload()
		staff.designation = "Warden"
		staff.save()
		roles = frappe.get_roles(staff.user)
		self.assertIn(WARDEN_ROLE, roles)
		self.assertNotIn(SECURITY_ROLE, roles)
		self.assertEqual(hostel_permissions(staff.user), set())

		# Deactivating the record disables the gate login and signs it out, like any other login.
		staff.reload()
		staff.designation = "Security"
		staff.save()
		staff.reload()
		staff.is_active = 0
		staff.save()
		self.assertEqual(frappe.db.get_value("User", staff.user, "enabled"), 0)
		self.assertEqual(session_count(staff.user), 0)
