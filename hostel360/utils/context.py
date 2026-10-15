"""Who is the current user, and what may they see?

Shared helpers used by every module and by the portal API. Portal users (students and
parents) never receive DocType permissions; every portal endpoint resolves the caller
through these helpers and checks ownership explicitly before touching data.
"""

import frappe
from frappe import _

from hostel360.setup.roles import (
	ADMIN_ROLES,
	PARENT_ROLE,
	STAFF_ROLES,
	STUDENT_ROLE,
)


def get_roles(user: str | None = None) -> set[str]:
	return set(frappe.get_roles(user or frappe.session.user))


def has_any_role(roles, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(get_roles(user).intersection(roles))


def is_staff(user: str | None = None) -> bool:
	"""Hostel staff with desk access (administrators, wardens, office and mess staff)."""
	return has_any_role(STAFF_ROLES, user)


def is_admin(user: str | None = None) -> bool:
	return has_any_role(ADMIN_ROLES, user)


def get_student_for_user(user: str | None = None) -> str | None:
	"""Name of the Hostel Student linked to the user, if any."""
	user = user or frappe.session.user
	if user in ("Guest", "Administrator"):
		return None
	return frappe.db.get_value("Hostel Student", {"user": user}, "name")


def get_guardian_for_user(user: str | None = None) -> str | None:
	"""Name of the Hostel Guardian linked to the user, if any."""
	user = user or frappe.session.user
	if user in ("Guest", "Administrator"):
		return None
	return frappe.db.get_value("Hostel Guardian", {"user": user}, "name")


def get_wards(guardian: str) -> list[str]:
	"""Students linked to a guardian through the Hostel Student Guardian child table."""
	if not guardian:
		return []
	rows = frappe.get_all(
		"Hostel Student Guardian",
		filters={"guardian": guardian, "parenttype": "Hostel Student"},
		pluck="parent",
		ignore_permissions=True,
	)
	return sorted(set(rows))


def get_guardians(student: str, leave_approvers_only: bool = False) -> list[str]:
	"""Guardians linked to a student."""
	filters = {"parent": student, "parenttype": "Hostel Student"}
	if leave_approvers_only:
		filters["can_approve_leave"] = 1
	return frappe.get_all(
		"Hostel Student Guardian", filters=filters, pluck="guardian", ignore_permissions=True
	)


def get_portal_identity(user: str | None = None) -> frappe._dict:
	"""Resolve a portal caller to a student or a guardian.

	Returns a dict with ``kind`` ("student" | "parent" | None), ``student``, ``guardian``
	and ``wards`` (students the caller may act for).
	"""
	user = user or frappe.session.user
	identity = frappe._dict(kind=None, student=None, guardian=None, wards=[])
	if user == "Guest":
		return identity

	roles = get_roles(user)
	if STUDENT_ROLE in roles:
		student = get_student_for_user(user)
		if student:
			identity.update(kind="student", student=student, wards=[student])
			return identity

	if PARENT_ROLE in roles:
		guardian = get_guardian_for_user(user)
		if guardian:
			identity.update(kind="parent", guardian=guardian, wards=get_wards(guardian))
	return identity


def require_portal_identity(kind: str | None = None) -> frappe._dict:
	"""Return the portal identity or raise PermissionError.

	``kind`` may restrict the caller to "student" or "parent".
	"""
	identity = get_portal_identity()
	if not identity.kind or (kind and identity.kind != kind):
		frappe.throw(_("You are not permitted to access this page."), frappe.PermissionError)
	return identity


def can_access_student(student: str, user: str | None = None) -> bool:
	"""True when the user is the student, a linked guardian, or hostel staff."""
	if not student:
		return False
	user = user or frappe.session.user
	if user == "Administrator" or is_staff(user):
		return True
	identity = get_portal_identity(user)
	return student in identity.wards


def assert_can_access_student(student: str, user: str | None = None) -> None:
	if not can_access_student(student, user):
		frappe.throw(_("You are not permitted to access this student's records."), frappe.PermissionError)
