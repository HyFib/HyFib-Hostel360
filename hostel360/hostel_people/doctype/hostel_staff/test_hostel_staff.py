# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.testing import make_block, make_hostel
from hostel360.setup.roles import DEPUTY_WARDEN_ROLE, OFFICE_ROLE, WARDEN_ROLE


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
		with self.assertRaises(frappe.ValidationError):
			make_staff("Security", [self.hostel_a])
		staff = make_staff("Security", [self.hostel_a], create_user=0)
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
