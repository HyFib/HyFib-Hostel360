"""Small helpers used across the fee module."""

import frappe
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


def get_portal_link(path: str = "") -> str:
	"""Absolute link into the student/parent portal, for messages and receipts."""
	base = (get_hostel_setting("portal_url") or get_url()).rstrip("/")
	path = path if path.startswith("/") else f"/{path}"
	return f"{base}/hostel{path}" if path != "/" else f"{base}/hostel"


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


def get_student_print_details(student: str) -> frappe._dict:
	"""Student particulars shown on receipts, invoices and the fee structure."""
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
	details.hostel_name = (
		frappe.db.get_value("Hostel", details.current_hostel, "hostel_name")
		if details.get("current_hostel")
		else None
	) or details.get("current_hostel")
	details.room = (
		frappe.db.get_value("Hostel Room", details.current_room, "room_number")
		if details.get("current_room")
		else None
	) or details.get("current_room")
	return details
