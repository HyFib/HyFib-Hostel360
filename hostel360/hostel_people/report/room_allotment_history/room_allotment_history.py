# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, nowdate

from hostel360.utils.validation import validate_date_range


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_date_range(filters.get("from_date"), filters.get("to_date"))
	rows = get_allotments(filters)
	today = getdate(nowdate())
	for row in rows:
		if row.status == "Cancelled":
			row.days = 0
			continue
		if row.status == "Active":
			end = min(today, getdate(row.to_date)) if row.to_date else today
		else:
			end = row.vacated_on or row.to_date or today
		row.days = max(date_diff(end, row.from_date) + 1, 0)
	return get_columns(), rows, None, None, get_summary(rows)


def get_allotments(filters) -> list[dict]:
	query_filters = [["docstatus", "!=", 0]]
	for fieldname in ("hostel", "room", "bed", "student", "status", "allotment_type", "academic_year"):
		if filters.get(fieldname):
			query_filters.append([fieldname, "=", filters.get(fieldname)])
	if filters.get("to_date"):
		query_filters.append(["from_date", "<=", filters.to_date])

	or_filters = None
	if filters.get("from_date"):
		# Allotments that overlap the period: still open, or ending on/after its start.
		or_filters = [["to_date", ">=", filters.from_date], ["to_date", "is", "not set"]]

	return frappe.get_list(
		"Room Allotment",
		filters=query_filters,
		or_filters=or_filters,
		fields=[
			"name",
			"student",
			"student_name",
			"hostel",
			"room",
			"bed",
			"allotment_type",
			"from_date",
			"to_date",
			"status",
			"vacated_on",
			"vacate_reason",
			"previous_allotment",
			"academic_year",
		],
		order_by="from_date desc, creation desc",
		limit_page_length=0,
	)


def get_columns() -> list[dict]:
	return [
		{
			"label": _("Allotment"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Room Allotment",
			"width": 150,
		},
		{
			"label": _("Student"),
			"fieldname": "student",
			"fieldtype": "Link",
			"options": "Hostel Student",
			"width": 120,
		},
		{"label": _("Name"), "fieldname": "student_name", "fieldtype": "Data", "width": 170},
		{"label": _("Hostel"), "fieldname": "hostel", "fieldtype": "Link", "options": "Hostel", "width": 80},
		{
			"label": _("Room"),
			"fieldname": "room",
			"fieldtype": "Link",
			"options": "Hostel Room",
			"width": 100,
		},
		{"label": _("Bed"), "fieldname": "bed", "fieldtype": "Link", "options": "Hostel Bed", "width": 110},
		{"label": _("Type"), "fieldname": "allotment_type", "fieldtype": "Data", "width": 80},
		{"label": _("From"), "fieldname": "from_date", "fieldtype": "Date", "width": 100},
		{"label": _("To"), "fieldname": "to_date", "fieldtype": "Date", "width": 100},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Vacated On"), "fieldname": "vacated_on", "fieldtype": "Date", "width": 100},
		{"label": _("Days"), "fieldname": "days", "fieldtype": "Int", "width": 70},
		{"label": _("Reason"), "fieldname": "vacate_reason", "fieldtype": "Data", "width": 220},
		{
			"label": _("Previous Allotment"),
			"fieldname": "previous_allotment",
			"fieldtype": "Link",
			"options": "Room Allotment",
			"width": 150,
		},
		{
			"label": _("Academic Year"),
			"fieldname": "academic_year",
			"fieldtype": "Link",
			"options": "Hostel Academic Year",
			"width": 100,
		},
	]


def get_summary(rows: list[dict]) -> list[dict]:
	stays = [row.days for row in rows if row.status in ("Vacated", "Transferred") and row.days]
	return [
		{"value": len(rows), "label": _("Allotments"), "datatype": "Int", "indicator": "Blue"},
		{
			"value": sum(1 for r in rows if r.status == "Active"),
			"label": _("Active"),
			"datatype": "Int",
			"indicator": "Green",
		},
		{
			"value": sum(1 for r in rows if r.allotment_type == "Transfer"),
			"label": _("Transfers"),
			"datatype": "Int",
			"indicator": "Blue",
		},
		{
			"value": sum(1 for r in rows if r.status == "Vacated"),
			"label": _("Vacated"),
			"datatype": "Int",
			"indicator": "Gray",
		},
		{
			"value": flt(sum(stays) / len(stays), 1) if stays else 0,
			"label": _("Average Completed Stay (days)"),
			"datatype": "Float",
			"indicator": "Blue",
		},
	]
