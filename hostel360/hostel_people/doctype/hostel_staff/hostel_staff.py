# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address

from hostel360.hostel_people.protection import keep_system_fields
from hostel360.setup.roles import DESIGNATION_ROLES, HOSTEL_SCOPED_DESIGNATIONS, PORTAL_ROLES
from hostel360.utils.validation import normalize_mobile

# Every role this controller may grant or take away. Roles outside this set (for example
# System Manager given by IT) are never touched.
MANAGED_ROLES = frozenset(role for roles in DESIGNATION_ROLES.values() for role in roles)


class HostelStaff(Document):
	def validate(self):
		# The linked user is only ever set by get_or_create_user; taking it from a request
		# would let a staff record take over (and disable) somebody else's account.
		keep_system_fields(self, ("user",))
		self.staff_name = " ".join((self.staff_name or "").split())
		self.mobile_number = normalize_mobile(self.mobile_number)
		if self.employee_id:
			self.employee_id = self.employee_id.strip().upper()
		if self.email:
			self.email = self.email.strip().lower()
			validate_email_address(self.email, throw=True)

		self.validate_assignments()
		if self.create_user or self.user:
			self.validate_login()

	def validate_assignments(self):
		seen = set()
		for row in self.assignments:
			key = (row.hostel, row.block or "")
			if key in seen:
				frappe.throw(_("Row {0}: this hostel and block are assigned twice.").format(row.idx))
			seen.add(key)
			if row.block and frappe.db.get_value("Hostel Block", row.block, "hostel") != row.hostel:
				frappe.throw(
					_("Row {0}: block {1} does not belong to hostel {2}.").format(
						row.idx, frappe.bold(row.block), frappe.bold(row.hostel)
					)
				)

	def validate_login(self):
		if not self.user:
			if not DESIGNATION_ROLES.get(self.designation):
				frappe.throw(
					_("{0} staff do not use the desk, so a login cannot be created.").format(_(self.designation))
				)
			if not self.email:
				frappe.throw(_("Enter an e-mail address to create a desk login."))

		if self.designation in HOSTEL_SCOPED_DESIGNATIONS and not self.assignments and self.is_active:
			# Without an assignment the user would see every hostel's records.
			frappe.throw(
				_("Assign at least one hostel to a {0} with a desk login.").format(_(self.designation)),
				title=_("Hostel Assignment Required"),
			)

	def on_update(self):
		if self.create_user and not self.user and self.is_active:
			self.db_set("user", self.get_or_create_user(), update_modified=False)
		if self.user:
			self.sync_user()
			self.sync_user_permissions()

	def on_trash(self):
		if self.user:
			frappe.db.set_value("User", self.user, "enabled", 0)
			remove_hostel_permissions(self.user)

	def get_or_create_user(self) -> str:
		existing = frappe.db.get_value(
			"User", {"name": self.email}, ["name", "user_type"], as_dict=True
		) or frappe.db.get_value("User", {"email": self.email}, ["name", "user_type"], as_dict=True)

		if existing:
			if existing.name in ("Administrator", "Guest"):
				frappe.throw(_("The {0} account cannot be linked to a staff record.").format(existing.name))
			other = frappe.db.get_value(
				"Hostel Staff", {"user": existing.name, "name": ["!=", self.name]}, "name"
			)
			if other:
				frappe.throw(
					_("User {0} is already linked to staff record {1}.").format(
						frappe.bold(existing.name), frappe.bold(other)
					)
				)
			if set(frappe.get_roles(existing.name)) & PORTAL_ROLES:
				frappe.throw(
					_("{0} is a student or parent portal account and cannot be used for staff.").format(
						frappe.bold(existing.name)
					)
				)
			return existing.name

		first_name, _sep, last_name = self.staff_name.partition(" ")
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": self.email,
				"first_name": first_name,
				"last_name": last_name,
				"user_type": "System User",
				"send_welcome_email": 1 if self.send_welcome_email else 0,
				"enabled": 1,
				"roles": [{"role": role} for role in DESIGNATION_ROLES[self.designation]],
			}
		)
		user.flags.ignore_permissions = True
		user.insert()
		return user.name

	def sync_user(self) -> None:
		"""Roles follow the designation; the login is disabled while the staff is inactive."""
		user = frappe.get_doc("User", self.user)
		desired = set(DESIGNATION_ROLES.get(self.designation, []))
		current = {row.role for row in user.roles}

		changed = False
		for row in list(user.roles):
			if row.role in MANAGED_ROLES and row.role not in desired:
				user.remove(row)
				changed = True
		for role in sorted(desired - current):
			user.append("roles", {"role": role})
			changed = True

		enabled = 1 if self.is_active else 0
		if user.enabled != enabled:
			user.enabled = enabled
			changed = True

		if changed:
			user.flags.ignore_permissions = True
			user.save()

	def sync_user_permissions(self) -> None:
		"""Restrict hostel-scoped designations to their assigned hostels."""
		if self.designation in HOSTEL_SCOPED_DESIGNATIONS:
			desired = sorted({row.hostel for row in self.assignments})
		else:
			desired = []
		set_hostel_permissions(self.user, desired)

	@frappe.whitelist(methods=["POST"])
	def resend_welcome_email(self) -> None:
		self.check_permission("write")
		if not self.user:
			frappe.throw(_("This staff member has no desk login yet."))
		user = frappe.get_doc("User", self.user)
		user.send_welcome_mail_to_user()


def set_hostel_permissions(user: str, hostels: list[str]) -> None:
	existing = {
		row.for_value: row.name
		for row in frappe.get_all(
			"User Permission",
			filters={"user": user, "allow": "Hostel"},
			fields=["name", "for_value"],
		)
	}

	for hostel, name in existing.items():
		if hostel not in hostels:
			frappe.delete_doc("User Permission", name, ignore_permissions=True)

	for hostel in hostels:
		if hostel in existing:
			continue
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user,
				"allow": "Hostel",
				"for_value": hostel,
				"apply_to_all_doctypes": 1,
				"is_default": 1 if len(hostels) == 1 else 0,
			}
		).insert(ignore_permissions=True)

	# A default hostel (pre-filled in new forms) only makes sense when there is exactly one.
	if hostels:
		for name, for_value, is_default in frappe.get_all(
			"User Permission",
			filters={"user": user, "allow": "Hostel"},
			fields=["name", "for_value", "is_default"],
			as_list=True,
		):
			wanted = 1 if len(hostels) == 1 and for_value == hostels[0] else 0
			if is_default != wanted:
				frappe.db.set_value("User Permission", name, "is_default", wanted)
		frappe.cache.hdel("user_permissions", user)


def remove_hostel_permissions(user: str) -> None:
	set_hostel_permissions(user, [])
