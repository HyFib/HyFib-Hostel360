"""Login, session and user hooks that keep portal accounts and staff accounts apart.

Portal users (students and parents) are Website Users that only ever reach the portal
SPA at /hostel. They must never hold a desk role, and they cannot sign in while their
portal is switched off, while the hostel office has not validated their registration
(tender 3.1; for parents: while none of their wards is a validated resident, 4.1), or
once their record has been rejected or closed. A portal user may exist before
validation (bulk import and the desk can create one early); this hook is what keeps it
closed until then.
"""

import frappe
from frappe import _

from hostel360.setup.roles import PARENT_ROLE, PORTAL_ROLES, STUDENT_ROLE

PORTAL_HOME = "hostel"
DESK_HOME = "staff"
CLOSED_STUDENT_STATUSES = ("Vacated", "Alumni")
# Roles every user carries implicitly; they say nothing about desk access.
AUTOMATIC_ROLES = frozenset({"All", "Guest", "Desk User", "Administrator"})


def get_website_user_home_page(user: str) -> str | None:
	"""Portal users land on the portal; staff land on their desk home (``/staff``)."""
	if not user or user == "Guest":
		return None
	user_type = frappe.get_cached_value("User", user, "user_type")
	if user_type == "Website User" and PORTAL_ROLES.intersection(frappe.get_roles(user)):
		return PORTAL_HOME
	if user_type == "System User":
		return DESK_HOME
	return None


def on_login(login_manager) -> None:
	"""Refuse portal sign-in when the portal is off or the hostel record is closed."""
	user = getattr(login_manager, "user", None)
	if not user or user in ("Guest", "Administrator"):
		return
	roles = set(frappe.get_roles(user))
	if not PORTAL_ROLES.intersection(roles):
		return

	reason = portal_login_blocked_reason(user, roles)
	if reason:
		from hostel360.auth.otp import security_log

		security_log(
			"portal_login_blocked", ip=getattr(frappe.local, "request_ip", None), user_hash=_user_hash(user)
		)
		frappe.throw(reason, frappe.AuthenticationError)


def portal_login_blocked_reason(user: str, roles: set[str] | None = None) -> str | None:
	"""Why this portal user may not sign in (or keep using the portal) right now, or None.

	The portal API can ask the same question for a session that already exists, to show
	the user the reason instead of a bare permission error.
	"""
	from hostel360.auth.otp import guardian_has_portal_ward, portal_enabled, student_may_sign_in

	roles = roles if roles is not None else set(frappe.get_roles(user))
	if STUDENT_ROLE in roles:
		if not portal_enabled("student_portal_enabled"):
			return _("The student portal is not available right now. Please try again later.")
		student = (
			frappe.db.get_value(
				"Hostel Student", {"user": user}, ["status", "validation_status"], as_dict=True
			)
			if frappe.db.exists("DocType", "Hostel Student")
			else None
		)
		if not student:
			return _(
				"Your portal account is not linked to a hostel record. Please contact the hostel office."
			)
		if student.validation_status == "Rejected" or student.status in CLOSED_STUDENT_STATUSES:
			return _(
				"Portal access for this hostel record has been closed. Please contact the hostel office."
			)
		if not student_may_sign_in(student):
			return _(
				"Your hostel registration has not been validated by the hostel office yet. "
				"You can sign in once it has been validated."
			)
		return None

	if PARENT_ROLE in roles:
		if not portal_enabled("parent_portal_enabled"):
			return _("The parent portal is not available right now. Please try again later.")
		guardian = (
			frappe.db.get_value(
				"Hostel Guardian", {"user": user}, ["name", "validation_status"], as_dict=True
			)
			if frappe.db.exists("DocType", "Hostel Guardian")
			else None
		)
		if not guardian:
			return _(
				"Your portal account is not linked to a hostel record. Please contact the hostel office."
			)
		if guardian.validation_status == "Rejected":
			return _(
				"Portal access for this parent record has been closed. Please contact the hostel office."
			)
		if not guardian_has_portal_ward(guardian.name):
			return _(
				"The parent portal opens once your ward's hostel registration has been validated "
				"by the hostel office."
			)
	return None


def on_session_creation(login_manager) -> None:
	"""Record portal sign-ins in the security log; nothing here may block a session."""
	try:
		user = getattr(login_manager, "user", None)
		if not user or user in ("Guest", "Administrator"):
			return
		roles = set(frappe.get_roles(user))
		if PORTAL_ROLES.intersection(roles):
			from hostel360.auth.otp import security_log

			kind = "student" if STUDENT_ROLE in roles else "parent"
			security_log(
				"portal_session_started",
				ip=getattr(frappe.local, "request_ip", None),
				kind=kind,
				user_hash=_user_hash(user),
			)
	except Exception:
		frappe.log_error(title="Hostel360: session logging failed", message=frappe.get_traceback())


def validate_user(doc, method=None) -> None:
	"""A portal user may not hold desk roles and must stay a Website User.

	On a demonstration site the published demo accounts are also kept as the demo command
	set them (``hostel360.demo.accounts``).
	"""
	if doc.name in ("Administrator", "Guest"):
		return
	from hostel360.demo.accounts import validate_demo_user

	validate_demo_user(doc)
	roles = {row.role for row in doc.get("roles") or [] if row.role}
	portal_roles = roles & PORTAL_ROLES
	if not portal_roles:
		return

	other_roles = roles - PORTAL_ROLES - AUTOMATIC_ROLES
	desk_roles = (
		sorted(
			frappe.get_all(
				"Role",
				filters={"name": ("in", list(other_roles)), "desk_access": 1},
				pluck="name",
			)
		)
		if other_roles
		else []
	)
	if desk_roles:
		frappe.throw(
			_(
				"{0} is a student or parent portal account and cannot also have desk roles ({1}). "
				"Create a separate staff account instead."
			).format(frappe.bold(doc.name), ", ".join(desk_roles)),
			title=_("Portal Account"),
		)
	if doc.user_type != "Website User":
		doc.user_type = "Website User"


def is_portal_user(user: str | None = None) -> bool:
	"""True for student and parent accounts (Website Users holding a portal role)."""
	user = user or frappe.session.user
	if not user or user in ("Guest", "Administrator"):
		return False
	if frappe.get_cached_value("User", user, "user_type") != "Website User":
		return False
	return bool(PORTAL_ROLES.intersection(frappe.get_roles(user)))


def _user_hash(user: str) -> str:
	import hashlib

	return hashlib.sha256(user.encode()).hexdigest()[:12]
