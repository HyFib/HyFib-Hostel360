"""Helpers shared by the Student Services DocTypes, services, reports and the approvals page."""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_url

from hostel360.setup.roles import (
	ADMIN_ROLE,
	ADMIN_ROLES,
	DEPUTY_WARDEN_ROLE,
	STAFF_ROLES,
	WARDEN_ROLE,
)

# Defaults mirror Hostel Settings so the module behaves sensibly before the settings are saved.
SETTINGS_DEFAULTS = {
	"leave_requires_parent_approval": 1,
	"leave_return_grace_hours": 2,
	"grievance_escalation_hours": 48,
	"grievance_urgent_escalation_hours": 4,
	"bonafide_validity_days": 90,
}

# Roles that decide leave and bonafide requests for every hostel. Deputy wardens decide
# only for the hostels they are assigned to (User Permission on Hostel).
GLOBAL_APPROVER_ROLES = frozenset({WARDEN_ROLE, *ADMIN_ROLES})

STUDENT_FIELDS = (
	"name",
	"full_name",
	"status",
	"validation_status",
	"current_hostel",
	"current_room",
	"current_bed",
	"current_allotment",
	"program",
	"department",
	"year_of_study",
	"academic_year",
	"gender",
	"user",
)


def in_migration() -> bool:
	"""True while historical records are being loaded (see the migration contract)."""
	return bool(frappe.flags.hostel360_migration)


def get_setting(fieldname: str, default=None):
	"""Value from Hostel Settings, falling back to the module default when unset."""
	if default is None:
		default = SETTINGS_DEFAULTS.get(fieldname)
	try:
		value = frappe.get_cached_doc("Hostel Settings").get(fieldname)
	except frappe.DoesNotExistError:
		return default
	if value is None or value == "":
		return default
	return value


def get_mess_setting(fieldname: str, default=None):
	try:
		value = frappe.get_cached_doc("Mess Settings").get(fieldname)
	except frappe.DoesNotExistError:
		return default
	if value is None or value == "":
		return default
	return value


def get_student(student: str) -> frappe._dict:
	"""Fields of a Hostel Student that Student Services relies on."""
	if not student:
		frappe.throw(_("Please select a student."), frappe.MandatoryError)
	meta = frappe.get_meta("Hostel Student")
	fields = [f for f in STUDENT_FIELDS if f == "name" or meta.has_field(f)]
	row = frappe.db.get_value("Hostel Student", student, fields, as_dict=True)
	if not row:
		frappe.throw(_("Student {0} was not found.").format(student), frappe.DoesNotExistError)
	return row


def assert_student_eligible(student: frappe._dict, service: str) -> None:
	"""Only active, validated students with a room may use hostel services (requirement 3.1).

	``service`` names what is being asked for, e.g. _("Leave"), and starts each message.
	"""
	if student.get("status") != "Active":
		frappe.throw(
			_("{0} is not available because the student's status is {1}.").format(
				service, _(student.get("status") or "not set")
			)
		)
	if student.get("validation_status") != "Validated":
		frappe.throw(
			_("{0} is available once the hostel office has validated the student's registration.").format(service)
		)
	if not student.get("current_hostel"):
		frappe.throw(_("{0} is not available because no room is allotted to the student.").format(service))


def get_user_hostels(user: str) -> set[str]:
	"""Hostels the user is assigned to through User Permissions."""
	return set(
		frappe.get_all(
			"User Permission",
			filters={"user": user, "allow": "Hostel"},
			pluck="for_value",
			ignore_permissions=True,
		)
	)


def is_warden_approver(user: str, hostel: str | None) -> bool:
	"""May the user decide leave and certificate requests for students of this hostel?"""
	if not user or user == "Guest":
		return False
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if roles & GLOBAL_APPROVER_ROLES:
		return True
	if DEPUTY_WARDEN_ROLE in roles and hostel:
		return hostel in get_user_hostels(user)
	return False


def assert_warden_approver(user: str, hostel: str | None) -> None:
	if not is_warden_approver(user, hostel):
		frappe.throw(
			_("Only the Warden, a Hostel Administrator or a Deputy Warden of {0} can decide this request.").format(
				hostel or _("this hostel")
			),
			frappe.PermissionError,
		)


def get_role_users(roles) -> list[str]:
	"""Enabled desk users holding any of the roles (Administrator excluded)."""
	holders = frappe.get_all(
		"Has Role",
		filters={"role": ["in", list(roles)], "parenttype": "User"},
		pluck="parent",
		distinct=True,
		ignore_permissions=True,
	)
	holders = [u for u in holders if u not in ("Administrator", "Guest")]
	if not holders:
		return []
	return frappe.get_all(
		"User",
		filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"},
		pluck="name",
		order_by="name asc",
		ignore_permissions=True,
	)


def get_warden_approvers(hostel: str | None) -> list[str]:
	"""Users who decide leave for a hostel: Wardens, Hostel Administrators and its Deputy Wardens."""
	users = set(get_role_users({WARDEN_ROLE, ADMIN_ROLE}))
	if hostel:
		deputies = get_role_users({DEPUTY_WARDEN_ROLE})
		if deputies:
			scoped = frappe.get_all(
				"User Permission",
				filters={"user": ["in", deputies], "allow": "Hostel", "for_value": hostel},
				pluck="user",
				ignore_permissions=True,
			)
			users.update(scoped)
	return sorted(users)


def get_senior_wardens() -> list[str]:
	"""Wardens and Hostel Administrators: recipients of confidential and escalated matters."""
	return get_role_users({WARDEN_ROLE, ADMIN_ROLE})


def is_staff_user(user: str) -> bool:
	if user == "Administrator":
		return True
	return bool(set(frappe.get_roles(user)) & STAFF_ROLES)


def get_portal_base_url() -> str:
	"""Public base URL used in links printed on certificates and sent in messages."""
	url = (get_setting("portal_url") or "").strip().rstrip("/")
	return url or get_url().rstrip("/")


def get_verification_url(code: str) -> str:
	return f"{get_portal_base_url()}/verify-certificate?code={code}"


def mask_name(full_name: str | None) -> str:
	"""Shows enough of a name to recognise it without disclosing it: "Rajesh Kannan" -> "R***** K"."""
	parts = (full_name or "").split()
	if not parts:
		return ""
	first, rest = parts[0], parts[1:]
	masked = first[0] + "*" * (len(first) - 1)
	return " ".join([masked, *(p[0].upper() for p in rest if p)])


def stars_to_rating(value) -> float:
	"""Portal ratings arrive as 1-5 stars; Rating fields store a 0-1 fraction."""
	stars = cint(value)
	if stars < 0 or stars > 5:
		frappe.throw(_("Ratings must be between 1 and 5 stars."))
	return stars / 5


def rating_to_stars(value) -> int:
	return round(flt(value) * 5)


def user_display_name(user: str | None) -> str:
	if not user:
		return ""
	return frappe.utils.get_fullname(user) or user
