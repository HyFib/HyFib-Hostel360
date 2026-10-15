"""Row-level access to grievances.

Confidential grievances (always the case for ragging and harassment) are visible only to
the Warden, Hostel Administrators, System Managers and the staff member they are assigned
to. Mess Managers see mess and food complaints only. User Permissions on Hostel still apply
on top of these rules, so a deputy warden sees only their own hostels.
"""

import frappe

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


def grievance_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Return False to deny; True leaves the decision to role permissions."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if roles & CONFIDENTIAL_ROLES:
		return True
	if doc.get("assigned_to") and doc.get("assigned_to") == user:
		return True
	if roles & GENERAL_ROLES:
		return not doc.get("is_confidential")
	if MESS_MANAGER_ROLE in roles:
		return not doc.get("is_confidential") and doc.get("category") == MESS_CATEGORY
	return False
