"""Row-level access to Message Log.

A message about a confidential grievance (ragging and harassment complaints always are)
names the complainant through its recipient and its reference, even with the message
text hidden at permlevel 1. Such rows are visible only to the people who may open the
grievance itself: the Warden, Hostel Administrators, System Managers and the staff member
it is assigned to. Every other row is left to the DocType's role permissions.
"""

import frappe
from frappe.utils import cint

from hostel360.setup.roles import ADMIN_ROLE, WARDEN_ROLE

GRIEVANCE = "Hostel Grievance"
CONFIDENTIAL_ROLES = frozenset({WARDEN_ROLE, ADMIN_ROLE, "System Manager"})


def message_log_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	user = user or frappe.session.user
	if _sees_confidential(user):
		return ""
	# Values are escaped by the database driver. ifnull: a plain != is NULL for rows
	# without a reference and would hide them too.
	confidential = (
		"select `name` from `tabHostel Grievance` where `is_confidential` = 1"  # noqa: S608
		f" and ifnull(`assigned_to`, '') != {frappe.db.escape(user)}"
	)
	return (
		f"(ifnull(`tabMessage Log`.`reference_doctype`, '') != {frappe.db.escape(GRIEVANCE)}"
		f" or `tabMessage Log`.`reference_name` not in ({confidential}))"
	)


def message_log_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Return False to deny; True leaves the decision to role permissions."""
	user = user or frappe.session.user
	if doc.get("reference_doctype") != GRIEVANCE or not doc.get("reference_name"):
		return True
	if _sees_confidential(user):
		return True
	grievance = frappe.db.get_value(
		GRIEVANCE, doc.get("reference_name"), ["is_confidential", "assigned_to"], as_dict=True
	)
	if not grievance or not cint(grievance.is_confidential):
		return True
	return grievance.assigned_to == user


def _sees_confidential(user: str) -> bool:
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & CONFIDENTIAL_ROLES)
