"""Moving a restored demonstration snapshot to today.

The snapshot was taken at some moment; restored a day (or a month) later, every leave
"starting this afternoon", every due date "next week" and every alert "an hour ago" would
be in the past. ``shift_dates`` moves every date and time in Hostel360's records (and their
comments, versions and notifications) forward by the time since the snapshot: date-times by
the exact interval, dates by the number of calendar days. What was relative to "now" stays
relative to now.

Some records belong to the calendar instead: a menu is for a given week, a mess bill for a
given month, the academic year is the academic year. Those fields are left alone here, and
``hostel360.hostel_migration.demo_data.refresh_calendar`` adds this week's menus, last
month's mess bills and so on afterwards.
"""

import datetime

import frappe
from frappe.utils import get_datetime, getdate

# Calendar records: never moved (see the module docstring).
SKIPPED_DOCTYPES = frozenset({"Hostel Academic Year", "Migration Record Map", "Exit Export Log"})
ANCHORED_FIELDS = {
	"Mess Menu": frozenset({"week_start_date", "week_end_date"}),
	"Mess Menu Item": frozenset({"menu_date"}),
	"Mess Bill Run": frozenset({"from_date", "to_date"}),
	"Mess Bill": frozenset({"from_date", "to_date"}),
	"Monthly Performance Report": frozenset({"from_date", "to_date"}),
	"Hostel Mess": frozenset({"billed_upto"}),
}
# Frappe records that follow the Hostel360 records they belong to.
FRAPPE_DOCTYPES = ("Comment", "Version", "Notification Log", "ToDo")
DATE_TYPES = ("Date",)
DATETIME_TYPES = ("Datetime",)


def interval(taken_at, now=None) -> tuple[int, int]:
	"""(seconds, calendar days) from the snapshot to ``now``."""
	taken_at = get_datetime(taken_at)
	now = get_datetime(now) if now else frappe.utils.now_datetime()
	return int((now - taken_at).total_seconds()), (getdate(now) - getdate(taken_at)).days


def doctypes_to_shift() -> list[str]:
	modules = frappe.get_module_list("hostel360")
	names = frappe.get_all(
		"DocType",
		filters={"module": ("in", modules), "issingle": 0, "is_virtual": 0},
		pluck="name",
		order_by="name asc",
	)
	return [name for name in names if name not in SKIPPED_DOCTYPES] + list(FRAPPE_DOCTYPES)


def shift_dates(seconds: int, days: int, doctypes: list[str] | None = None) -> dict[str, int]:
	"""Move dates by ``days`` and date-times by ``seconds``. Returns rows touched per DocType.

	``doctypes`` limits the move to some DocTypes (default: every one ``doctypes_to_shift``
	lists).
	"""
	touched = {}
	if not seconds and not days:
		return touched
	for doctype in doctypes or doctypes_to_shift():
		if not frappe.db.table_exists(doctype):
			continue
		assignments = shift_assignments(doctype, seconds, days)
		if not assignments:
			continue
		# Column names come from the DocType definitions, never from input.
		frappe.db.sql(
			f"update `tab{doctype}` set {', '.join(assignments)}",  # noqa: S608
			{"seconds": seconds, "days": days},
		)
		touched[doctype] = frappe.db.sql("select row_count()")[0][0]
	return touched


def shift_assignments(doctype: str, seconds: int, days: int) -> list[str]:
	meta = frappe.get_meta(doctype)
	anchored = ANCHORED_FIELDS.get(doctype, frozenset())
	columns = set(frappe.db.get_table_columns(doctype))
	assignments = []
	for column, unit in (("creation", "SECOND"), ("modified", "SECOND")):
		if column in columns and seconds:
			assignments.append(_add(column, unit))
	for field in meta.fields:
		if field.fieldname in anchored or field.fieldname not in columns:
			continue
		if field.fieldtype in DATETIME_TYPES and seconds:
			assignments.append(_add(field.fieldname, "SECOND"))
		elif field.fieldtype in DATE_TYPES and days:
			assignments.append(_add(field.fieldname, "DAY"))
	return assignments


def _add(column: str, unit: str) -> str:
	placeholder = "%(seconds)s" if unit == "SECOND" else "%(days)s"
	return f"`{column}` = date_add(`{column}`, interval {placeholder} {unit})"


def monday(date) -> datetime.date:
	date = getdate(date)
	return date - datetime.timedelta(days=date.weekday())
