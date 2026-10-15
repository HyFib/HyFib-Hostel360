# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from collections import Counter

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, get_first_day, getdate, nowdate, time_diff_in_hours

APPROVED_STATUSES = ("Approved", "Checked Out", "Returned", "Overdue")
PENDING_STATUSES = ("Pending Parent Approval", "Pending Warden Approval")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.from_date or get_first_day(nowdate()))
	to_date = getdate(filters.to_date or nowdate())
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))

	rows = get_rows(filters, from_date, to_date)
	return get_columns(), rows, None, get_chart(rows), get_summary(rows)


def get_rows(filters, from_date, to_date):
	conditions = [
		["from_datetime", "<", f"{add_days(to_date, 1)} 00:00:00"],
		["to_datetime", ">=", f"{from_date} 00:00:00"],
	]
	for fieldname in ("hostel", "status", "student", "applied_by"):
		if filters.get(fieldname):
			conditions.append([fieldname, "=", filters.get(fieldname)])

	leaves = frappe.get_list(
		"Home Visit Leave",
		filters=conditions,
		fields=[
			"name",
			"student",
			"student_name",
			"hostel",
			"room",
			"from_datetime",
			"to_datetime",
			"number_of_days",
			"destination",
			"reason",
			"applied_by",
			"parent_approval_status",
			"warden_approval_status",
			"warden_user",
			"status",
			"checked_out_on",
			"returned_on",
			"mess_reduction",
		],
		order_by="from_datetime desc",
		limit_page_length=0,
	)
	for leave in leaves:
		leave["late_by_hours"] = None
		if leave.returned_on and get_datetime(leave.returned_on) > get_datetime(leave.to_datetime):
			leave["late_by_hours"] = flt(time_diff_in_hours(leave.returned_on, leave.to_datetime), 1)
	return leaves


def get_columns():
	return [
		{"fieldname": "name", "label": _("Leave"), "fieldtype": "Link", "options": "Home Visit Leave", "width": 150},
		{"fieldname": "student", "label": _("Student"), "fieldtype": "Link", "options": "Hostel Student", "width": 120},
		{"fieldname": "student_name", "label": _("Student Name"), "fieldtype": "Data", "width": 170},
		{"fieldname": "hostel", "label": _("Hostel"), "fieldtype": "Link", "options": "Hostel", "width": 90},
		{"fieldname": "room", "label": _("Room"), "fieldtype": "Link", "options": "Hostel Room", "width": 100},
		{"fieldname": "from_datetime", "label": _("Leaving On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "to_datetime", "label": _("Returning On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "number_of_days", "label": _("Days"), "fieldtype": "Int", "width": 60},
		{"fieldname": "destination", "label": _("Destination"), "fieldtype": "Data", "width": 130},
		{"fieldname": "reason", "label": _("Reason"), "fieldtype": "Data", "width": 160},
		{"fieldname": "applied_by", "label": _("Applied By"), "fieldtype": "Data", "width": 90},
		{"fieldname": "parent_approval_status", "label": _("Parent"), "fieldtype": "Data", "width": 100},
		{"fieldname": "warden_approval_status", "label": _("Warden"), "fieldtype": "Data", "width": 90},
		{"fieldname": "warden_user", "label": _("Decided By"), "fieldtype": "Link", "options": "User", "width": 150},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 140},
		{"fieldname": "checked_out_on", "label": _("Checked Out"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "returned_on", "label": _("Returned"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "late_by_hours", "label": _("Late By (Hours)"), "fieldtype": "Float", "precision": 1, "width": 110},
		{
			"fieldname": "mess_reduction",
			"label": _("Mess Reduction"),
			"fieldtype": "Link",
			"options": "Mess Reduction Application",
			"width": 150,
		},
	]


def get_chart(rows):
	if not rows:
		return None
	counts = Counter(row.status for row in rows)
	labels = [status for status in (*PENDING_STATUSES, *APPROVED_STATUSES, "Rejected", "Cancelled") if counts.get(status)]
	return {
		"data": {"labels": [_(s) for s in labels], "datasets": [{"name": _("Leaves"), "values": [counts[s] for s in labels]}]},
		"type": "bar",
		"colors": ["#2490ef"],
	}


def get_summary(rows):
	counts = Counter(row.status for row in rows)
	late = sum(1 for row in rows if row.late_by_hours)
	return [
		{"label": _("Leaves"), "value": len(rows), "datatype": "Int", "indicator": "Blue"},
		{"label": _("Approved"), "value": sum(counts[s] for s in APPROVED_STATUSES), "datatype": "Int", "indicator": "Green"},
		{"label": _("Pending"), "value": sum(counts[s] for s in PENDING_STATUSES), "datatype": "Int", "indicator": "Orange"},
		{"label": _("Rejected"), "value": counts["Rejected"], "datatype": "Int", "indicator": "Red"},
		{"label": _("Overdue Now"), "value": counts["Overdue"], "datatype": "Int", "indicator": "Red"},
		{"label": _("Returned Late"), "value": late, "datatype": "Int", "indicator": "Orange"},
	]
