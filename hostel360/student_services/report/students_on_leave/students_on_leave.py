# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, now_datetime, time_diff_in_hours

CANDIDATE_STATUSES = ("Approved", "Checked Out", "Overdue", "Returned")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	as_of = get_datetime(filters.as_of) if filters.as_of else now_datetime()
	rows = get_rows(filters, as_of)
	return get_columns(), rows, None, None, get_summary(rows)


def get_rows(filters, as_of):
	conditions = [
		["status", "in", CANDIDATE_STATUSES],
		["from_datetime", "<=", as_of],
	]
	if filters.hostel:
		conditions.append(["hostel", "=", filters.hostel])
	leaves = frappe.get_list(
		"Home Visit Leave",
		filters=conditions,
		or_filters=[["returned_on", "is", "not set"], ["returned_on", ">", as_of]],
		fields=[
			"name",
			"student",
			"student_name",
			"hostel",
			"room",
			"from_datetime",
			"to_datetime",
			"checked_out_on",
			"returned_on",
			"destination",
			"contact_during_leave",
			"status",
		],
		order_by="hostel asc, room asc, student_name asc",
		limit_page_length=0,
	)

	rows = []
	for leave in leaves:
		movement = away_state(leave, as_of)
		if not movement:
			continue
		to_datetime = get_datetime(leave.to_datetime)
		overdue_hours = flt(time_diff_in_hours(as_of, to_datetime), 1) if as_of > to_datetime else None
		rows.append(
			{
				**leave,
				"left_on": leave.checked_out_on or leave.from_datetime,
				"movement": movement if not overdue_hours else _("Overdue"),
				"overdue_hours": overdue_hours,
			}
		)
	return rows


def away_state(leave, as_of) -> str | None:
	"""Why the student counts as away at ``as_of``, or None when they were in the hostel."""
	returned_on = get_datetime(leave.returned_on) if leave.returned_on else None
	if returned_on and returned_on <= as_of:
		return None
	if leave.checked_out_on:
		return _("Checked Out") if get_datetime(leave.checked_out_on) <= as_of else None
	# No gate record: rely on the approved dates. Overdue leaves stay away until a return is recorded.
	if leave.status == "Overdue" or (returned_on and returned_on > as_of):
		return _("Not Checked Out")
	if get_datetime(leave.from_datetime) <= as_of < get_datetime(leave.to_datetime):
		return _("Not Checked Out")
	return None


def get_columns():
	return [
		{
			"fieldname": "student",
			"label": _("Student"),
			"fieldtype": "Link",
			"options": "Hostel Student",
			"width": 120,
		},
		{"fieldname": "student_name", "label": _("Student Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "hostel", "label": _("Hostel"), "fieldtype": "Link", "options": "Hostel", "width": 90},
		{
			"fieldname": "room",
			"label": _("Room"),
			"fieldtype": "Link",
			"options": "Hostel Room",
			"width": 100,
		},
		{
			"fieldname": "name",
			"label": _("Leave"),
			"fieldtype": "Link",
			"options": "Home Visit Leave",
			"width": 150,
		},
		{"fieldname": "left_on", "label": _("Left On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "to_datetime", "label": _("Expected Back"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "destination", "label": _("Destination"), "fieldtype": "Data", "width": 130},
		{"fieldname": "contact_during_leave", "label": _("Contact"), "fieldtype": "Data", "width": 120},
		{"fieldname": "movement", "label": _("Movement"), "fieldtype": "Data", "width": 120},
		{
			"fieldname": "overdue_hours",
			"label": _("Overdue (Hours)"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 110,
		},
	]


def get_summary(rows):
	overdue = sum(1 for row in rows if row["overdue_hours"])
	not_checked_out = sum(1 for row in rows if not row.get("checked_out_on"))
	return [
		{"label": _("Students Away"), "value": len(rows), "datatype": "Int", "indicator": "Blue"},
		{
			"label": _("Overdue"),
			"value": overdue,
			"datatype": "Int",
			"indicator": "Red" if overdue else "Green",
		},
		{
			"label": _("No Check-out Record"),
			"value": not_checked_out,
			"datatype": "Int",
			"indicator": "Orange",
		},
	]
