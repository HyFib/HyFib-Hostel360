"""Small helpers used across the fee module."""

import frappe
from frappe import _
from frappe.utils import flt, get_url

from hostel360.hostel_fees.constants import SETTINGS

MONEY_PRECISION = 2
# Messages are rendered outside any user session, so dates use the institution's format explicitly.
DISPLAY_DATE_FORMAT = "dd-MM-yyyy"


def money(value) -> float:
	return flt(value, MONEY_PRECISION)


def is_migration() -> bool:
	"""True while legacy records are being imported (see design: historical data contract)."""
	return bool(frappe.flags.hostel360_migration)


def notifications_suppressed() -> bool:
	return bool(
		frappe.flags.hostel360_migration
		or frappe.flags.in_install
		or frappe.flags.in_migrate
		or frappe.flags.in_import
	)


def get_payment_settings():
	settings = frappe.get_cached_doc(SETTINGS)
	# A single loaded without a name cannot find its encrypted fields, which are stored under
	# the DocType's name (that is how the settings form saves them).
	if not settings.name:
		settings.name = SETTINGS
	return settings


def get_hostel_setting(fieldname: str):
	"""Read Hostel Settings defensively; the fee module must keep working on a half-configured site."""
	try:
		return frappe.db.get_single_value("Hostel Settings", fieldname)
	except Exception:
		return None


def get_current_academic_year() -> str | None:
	return get_hostel_setting("current_academic_year")


def get_institution_short_name() -> str:
	return get_hostel_setting("institution_short_name") or get_hostel_setting("institution_name") or ""


def get_site_base_url() -> str:
	"""Public base URL of the site (Hostel Settings' portal URL, else the site URL)."""
	return (get_hostel_setting("portal_url") or get_url()).rstrip("/")


def get_portal_link(path: str = "") -> str:
	"""Absolute link into the student/parent portal, for messages and receipts."""
	base = get_site_base_url()
	path = path if path.startswith("/") else f"/{path}"
	return f"{base}/hostel{path}" if path != "/" else f"{base}/hostel"


def get_student_hostel(student: str | None) -> str | None:
	"""The hostel a student's fee records belong to: the current one, else the last one lived in.

	Hostel-scoped staff see fee records through their ``hostel`` field, so a student who has
	vacated keeps the hostel they left instead of becoming visible to every hostel.
	"""
	if not student:
		return None
	try:
		from hostel360.hostel_people.services import get_student_hostel as people_student_hostel
	except ImportError:
		people_student_hostel = None
	if people_student_hostel:
		return people_student_hostel(student) or None

	fields = ["current_hostel"]
	if frappe.get_meta("Hostel Student").has_field("last_hostel"):
		fields.append("last_hostel")
	values = frappe.db.get_value("Hostel Student", student, fields, as_dict=True) or {}
	return values.get("current_hostel") or values.get("last_hostel") or None


def resolve_record_hostel(doc) -> str | None:
	"""The hostel a fee record is filed under. It is never taken from the request.

	Hostel-scoped staff see fee records through ``hostel``, so it always follows the student: the
	hostel they live in, else the one they last lived in. Two callers may file a record elsewhere:
	the legacy migration (historical records keep the hostel they were issued in) and server code
	that sets ``flags.keep_hostel`` (the mess bill run bills the hostel whose mess the student ate
	in). Flags cannot be set through the REST API.
	"""
	if doc.get("hostel") and (is_migration() or doc.flags.keep_hostel):
		return doc.hostel
	return get_student_hostel(doc.get("student"))


def assert_hostel_permitted(doc) -> None:
	"""Check a desk save again once the record's hostel has been derived from the student.

	Frappe checks create and write permission before ``validate`` runs, against the hostel the
	request sent. Without a second check a hostel-scoped user could file a record for another
	hostel's student by sending their own hostel.
	"""
	if doc.flags.ignore_permissions or is_migration():
		return
	if doc.has_permission("create" if doc.is_new() else "write"):
		return
	if doc.get("hostel"):
		message = _("Student {0} belongs to hostel {1}, which is not one of your assigned hostels.").format(
			doc.get("student"), doc.hostel
		)
	else:
		message = _("You are not permitted to save this {0}.").format(_(doc.doctype))
	frappe.throw(message, frappe.PermissionError, title=_("Not permitted"))


def persist_now() -> None:
	"""Commit evidence (failure reasons, alerts) before an exception rolls the request back.

	Skipped under the test runner, where every test relies on its own rollback.
	"""
	if not frappe.in_test:
		frappe.db.commit()  # nosemgrep


def get_permitted_hostels(doctype: str | None = None, user: str | None = None) -> list[str] | None:
	"""Hostels the user is restricted to through User Permissions; None means unrestricted.

	Script reports query with SQL, so they apply the hostel scoping that list views get for free.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	from frappe.permissions import get_user_permissions

	rows = get_user_permissions(user).get("Hostel") or []
	hostels = [
		row.get("doc")
		for row in rows
		if not row.get("applicable_for") or not doctype or row.get("applicable_for") == doctype
	]
	return hostels or None


def get_student_print_details(student: str, hostel: str | None = None) -> frappe._dict:
	"""Student particulars shown on receipts, invoices and the fee structure.

	``hostel`` is the document's own hostel. It is the one shown, because the student may have
	moved or vacated since the document was issued.
	"""
	details = frappe.db.get_value(
		"Hostel Student",
		student,
		[
			"name",
			"full_name",
			"register_number",
			"current_hostel",
			"current_room",
			"program",
			"year_of_study",
		],
		as_dict=True,
	) or frappe._dict(name=student)
	shown_hostel = hostel or details.get("current_hostel") or get_student_hostel(student)
	details.hostel_name = (
		frappe.db.get_value("Hostel", shown_hostel, "hostel_name") if shown_hostel else None
	) or shown_hostel
	# The room is only meaningful while the student still lives in the hostel shown.
	in_shown_hostel = details.get("current_hostel") and details.current_hostel == shown_hostel
	details.room = (
		(frappe.db.get_value("Hostel Room", details.current_room, "room_number") or details.current_room)
		if in_shown_hostel and details.get("current_room")
		else None
	)
	return details
