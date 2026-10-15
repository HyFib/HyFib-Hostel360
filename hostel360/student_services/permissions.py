"""Row-level access to Student Services records.

Confidential grievances (always the case for ragging and harassment) are visible only to
the Warden, Hostel Administrators, System Managers and the staff member they are assigned
to. Mess Managers see mess and food complaints only. User Permissions on Hostel still apply
on top of these rules, so a deputy warden sees only their own hostels.

Frappe asks these hooks about the document as a request sent it. For a record that already
exists, the values that decide access (its hostel, confidentiality and assignee) are read
from the database as well, so a request cannot open a record by rewriting them.
"""

import frappe
from frappe.utils import cint

from hostel360.setup.roles import (
	ADMIN_ROLE,
	AUDITOR_ROLE,
	DEPUTY_WARDEN_ROLE,
	MESS_MANAGER_ROLE,
	OFFICE_ROLE,
	WARDEN_ROLE,
)

CONFIDENTIAL_ROLES = frozenset({WARDEN_ROLE, ADMIN_ROLE, "System Manager"})
GENERAL_ROLES = frozenset({DEPUTY_WARDEN_ROLE, OFFICE_ROLE, AUDITOR_ROLE})
MESS_CATEGORY = "Mess and Food"
GRIEVANCE_ACCESS_FIELDS = ("hostel", "assigned_to", "is_confidential", "category")


def grievance_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	roles = set(frappe.get_roles(user))
	if roles & CONFIDENTIAL_ROLES:
		return ""

	assigned = f"`tabHostel Grievance`.`assigned_to` = {frappe.db.escape(user)}"
	if roles & GENERAL_ROLES:
		return f"(`tabHostel Grievance`.`is_confidential` = 0 or {assigned})"
	if MESS_MANAGER_ROLE in roles:
		category = frappe.db.escape(MESS_CATEGORY)
		return (
			f"((`tabHostel Grievance`.`is_confidential` = 0 and `tabHostel Grievance`.`category` = {category})"
			f" or {assigned})"
		)
	return assigned


def can_see_confidential(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & CONFIDENTIAL_ROLES)


def grievance_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Return False to deny; True leaves the decision to role permissions."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	stored = stored_values(doc, GRIEVANCE_ACCESS_FIELDS)
	if stored is None:
		# Being raised: confidentiality protects a grievance once it exists, and staff may
		# register a ragging complaint on a student's behalf.
		return True
	if not hostel_in_scope(stored.hostel, user, doc.doctype):
		return False
	roles = set(frappe.get_roles(user))
	if roles & CONFIDENTIAL_ROLES:
		return True
	# An existing grievance is judged as stored, so a request cannot assign itself or
	# declassify its way in. What a save may change is the controller's business: anyone
	# may make a grievance confidential, only the Warden may undo that.
	return grievance_visible(stored, user, roles)


def grievance_visible(values, user: str, roles: set[str]) -> bool:
	if values.get("assigned_to") and values.get("assigned_to") == user:
		return True
	if roles & GENERAL_ROLES:
		return not cint(values.get("is_confidential"))
	if MESS_MANAGER_ROLE in roles:
		return not cint(values.get("is_confidential")) and values.get("category") == MESS_CATEGORY
	return False


def hostel_scoped_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Hostel scoping judged on the stored record (Leave, Bonafide Certificate Request, Feedback)."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	stored = stored_values(doc, ("hostel",))
	return stored is None or hostel_in_scope(stored.hostel, user, doc.doctype)


def stored_values(doc, fieldnames) -> frappe._dict | None:
	"""Values of an existing record as saved; None for a record being created."""
	name = doc.get("name")
	is_new = doc.is_new() if hasattr(doc, "is_new") else doc.get("__islocal")
	if not name or is_new:
		return None
	return frappe.db.get_value(doc.doctype, name, list(fieldnames), as_dict=True)


def hostel_in_scope(hostel: str | None, user: str, doctype: str) -> bool:
	"""Same rule as Frappe's User Permissions on Hostel, applied to a stored value."""
	if not hostel:
		return True
	from frappe.permissions import get_user_permissions

	allowed = {
		row.get("doc")
		for row in get_user_permissions(user).get("Hostel") or []
		if not row.get("applicable_for") or row.get("applicable_for") == doctype
	}
	return not allowed or hostel in allowed
