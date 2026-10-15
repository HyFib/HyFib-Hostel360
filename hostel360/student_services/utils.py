"""Helpers shared by the Student Services DocTypes, services, reports and the approvals page."""

import frappe
from frappe import _
from frappe.model import no_value_fields, numeric_fieldtypes, table_fields
from frappe.utils import cint, cstr, flt, get_datetime, get_timedelta, get_url, getdate

from hostel360.setup.roles import (
	ADMIN_ROLE,
	ADMIN_ROLES,
	DEPUTY_WARDEN_ROLE,
	GRIEVANCE_HANDLER_ROLES,
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
	"last_hostel",
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


def student_hostel(student: frappe._dict) -> str | None:
	"""The hostel a student's records belong to: the current one, or the last one after vacating.

	Records of a student who has left still belong to that hostel, so hostel-scoped staff of
	other hostels do not see them.
	"""
	return student.get("current_hostel") or student.get("last_hostel") or None


def people_service(name: str):
	"""A Hostel People service function, or None on a site where it is not available."""
	try:
		from hostel360.hostel_people import services
	except ImportError:
		return None
	return getattr(services, name, None)


def get_student_hostel(student: str | None) -> str | None:
	if not student:
		return None
	helper = people_service("get_student_hostel")
	if helper:
		return helper(student)
	meta = frappe.get_meta("Hostel Student")
	fields = [f for f in ("current_hostel", "last_hostel") if meta.has_field(f)]
	row = frappe.db.get_value("Hostel Student", student, fields, as_dict=True)
	return student_hostel(row) if row else None


def get_student_block(student: str | None) -> str | None:
	"""Block of the room the student lives in now (None when no room is allotted)."""
	if not student:
		return None
	helper = people_service("get_student_block")
	if helper:
		return helper(student)
	room = frappe.db.get_value("Hostel Student", student, "current_room")
	return frappe.db.get_value("Hostel Room", room, "block") if room else None


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


def staff_covers_student(user: str, student: str | None, hostel: str | None) -> bool:
	"""Block-level reach of a hostel-scoped staff member over a student of ``hostel``.

	Hostel-level scoping comes from User Permissions and is checked by the caller. Deputy
	wardens may also be assigned to particular blocks; Hostel People decides that from the
	staff assignments. Without that service the hostel-level check is all there is.
	"""
	covers = people_service("staff_covers_student")
	if not covers or not student:
		return True
	# A record made before the student moved hostels keeps the old hostel; the student's
	# present block says nothing about who looks after that record.
	if get_student_hostel(student) != hostel:
		return True
	return bool(covers(user, student))


def is_warden_approver(user: str, hostel: str | None, student: str | None = None) -> bool:
	"""May the user decide leave and certificate requests for this student of this hostel?"""
	if not user or user == "Guest":
		return False
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if roles & GLOBAL_APPROVER_ROLES:
		return True
	if DEPUTY_WARDEN_ROLE in roles and hostel:
		return hostel in get_user_hostels(user) and staff_covers_student(user, student, hostel)
	return False


def assert_warden_approver(user: str, hostel: str | None, student: str | None = None) -> None:
	if not is_warden_approver(user, hostel, student):
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


def get_warden_approvers(hostel: str | None, student: str | None = None) -> list[str]:
	"""Users who decide leave for a student of a hostel: Wardens, Hostel Administrators and
	the Deputy Wardens assigned to that hostel (and to the student's block, where deputies
	are assigned block by block)."""
	users = set(get_role_users({WARDEN_ROLE, ADMIN_ROLE}))
	if hostel:
		users.update(get_hostel_deputies(hostel, student))
	return sorted(users)


def get_hostel_deputies(hostel: str, student: str | None = None) -> list[str]:
	scoped_users = people_service("get_scoped_staff_users")
	if scoped_users:
		block = get_student_block(student) if student and get_student_hostel(student) == hostel else None
		return list(scoped_users(DEPUTY_WARDEN_ROLE, hostel, block) or [])

	deputies = get_role_users({DEPUTY_WARDEN_ROLE})
	if not deputies:
		return []
	return frappe.get_all(
		"User Permission",
		filters={"user": ["in", deputies], "allow": "Hostel", "for_value": hostel},
		pluck="user",
		ignore_permissions=True,
	)


def get_senior_wardens() -> list[str]:
	"""Wardens and Hostel Administrators: recipients of confidential and escalated matters."""
	return get_role_users({WARDEN_ROLE, ADMIN_ROLE})


def is_staff_user(user: str) -> bool:
	if user == "Administrator":
		return True
	return bool(set(frappe.get_roles(user)) & STAFF_ROLES)


def is_grievance_handler(user: str) -> bool:
	"""Staff who may be assigned to and act on grievances. Auditors only observe."""
	if user == "Administrator":
		return True
	return bool(set(frappe.get_roles(user)) & GRIEVANCE_HANDLER_ROLES)


# ---------------------------------------------------------------------- server-owned fields
#
# Frappe enforces ``read_only`` in the form only, and checks permission on a document as the
# request sent it, before ``validate`` runs. Values the controllers derive (the student's
# hostel) or that only an action may set (a decision, a timeline) are therefore guarded here.


def value_changed(doc, before, fieldname: str) -> bool:
	"""Like ``has_value_changed``, but blind to how a client spells an unchanged value
	(``None`` against ``""``, ``0`` against ``None``, a date string against a date)."""
	df = doc.meta.get_field(fieldname)
	fieldtype = df.fieldtype if df else "Data"
	old, new = before.get(fieldname), doc.get(fieldname)
	if fieldtype in table_fields:
		return table_signature(old, df.options) != table_signature(new, df.options)
	return normalised(fieldtype, old) != normalised(fieldtype, new)


def normalised(fieldtype: str, value):
	if fieldtype in numeric_fieldtypes or fieldtype == "Rating":
		return flt(value)
	if value in (None, ""):
		return ""
	if fieldtype == "Date":
		return getdate(value)
	if fieldtype == "Datetime":
		return get_datetime(value)
	if fieldtype == "Time":
		return get_timedelta(value)
	return cstr(value)


def table_signature(rows, child_doctype: str) -> list[tuple]:
	# Fetched columns (a user's full name, say) are refreshed by Frappe on every save and say
	# nothing about whether the rows were edited.
	fields = [
		df
		for df in frappe.get_meta(child_doctype).fields
		if df.fieldtype not in no_value_fields and not df.fetch_from
	]
	return [
		(cstr(row.get("name")), *(normalised(df.fieldtype, row.get(df.fieldname)) for df in fields))
		for row in rows or []
	]


def changed_fields(doc, fieldnames) -> list[str]:
	before = doc.get_doc_before_save()
	if doc.is_new() or not before:
		return []
	return [fieldname for fieldname in fieldnames if value_changed(doc, before, fieldname)]


def assert_unchanged(doc, fieldnames, message: str) -> None:
	"""Refuse a save that changes any of ``fieldnames``; ``message`` gets the field labels as {0}."""
	changed = changed_fields(doc, fieldnames)
	if changed:
		labels = ", ".join(_(doc.meta.get_label(fieldname)) for fieldname in changed)
		frappe.throw(message.format(labels), frappe.PermissionError, title=_("Not Allowed"))


def recheck_scope(doc) -> None:
	"""Check the user's access again once ``validate`` has settled the derived values.

	Frappe has already checked permission on the document as the request sent it. Hostel
	scoping and the grievance confidentiality rules depend on values a request can change,
	so the stored record and the values about to be saved are both checked here. A failure
	rolls the request back before anything is written or returned.
	"""
	if doc.flags.ignore_permissions or in_migration() or frappe.session.user == "Administrator":
		return
	if doc.is_new():
		doc.check_permission("create")
		return
	before = doc.get_doc_before_save()
	if before and not frappe.has_permission(doc.doctype, "write", doc=before):
		doc.raise_no_permission_to("write")
	doc.check_permission("write")


def load_for_action(doc):
	"""The stored record, locked, for an action started from a desk form.

	Whitelisted controller methods run on the copy of the document the browser sent, and
	Frappe only checks that it is not stale. Decisions are taken on what is in the database.
	"""
	stored = frappe.get_doc(doc.doctype, doc.name, for_update=True)
	if not frappe.has_permission(stored.doctype, "read", doc=stored):
		stored.raise_no_permission_to("read")
	return stored


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
