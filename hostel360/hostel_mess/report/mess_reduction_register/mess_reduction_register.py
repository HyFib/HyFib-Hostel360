# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	query_filters = {}
	for field in ("mess", "hostel", "student", "status", "reason"):
		if filters.get(field):
			query_filters[field] = filters.get(field)
	# Applications whose absence overlaps the chosen period.
	if filters.from_date:
		query_filters["to_date"] = [">=", getdate(filters.from_date)]
	if filters.to_date:
		query_filters["from_date"] = ["<=", getdate(filters.to_date)]

	# get_list applies the viewer's permissions, so deputy wardens see only their hostels.
	data = frappe.get_list(
		"Mess Reduction Application",
		filters=query_filters,
		fields=[
			"name",
			"student",
			"student_name",
			"hostel",
			"mess",
			"from_date",
			"to_date",
			"number_of_days",
			"reason",
			"status",
			"applied_by",
			"creation as applied_on",
			"approver",
			"decided_on",
			"approval_remarks",
		],
		order_by="from_date desc, creation desc",
		limit_page_length=0,
	)
	return get_columns(), data, None, get_chart(data), get_summary(data)


def get_columns():
	return [
		{
			"fieldname": "name",
			"label": _("Application"),
			"fieldtype": "Link",
			"options": "Mess Reduction Application",
			"width": 150,
		},
		{
			"fieldname": "student",
			"label": _("Student"),
			"fieldtype": "Link",
			"options": "Hostel Student",
			"width": 130,
		},
		{"fieldname": "student_name", "label": _("Student Name"), "fieldtype": "Data", "width": 170},
		{"fieldname": "hostel", "label": _("Hostel"), "fieldtype": "Link", "options": "Hostel", "width": 90},
		{
			"fieldname": "mess",
			"label": _("Mess"),
			"fieldtype": "Link",
			"options": "Hostel Mess",
			"width": 120,
		},
		{"fieldname": "from_date", "label": _("From"), "fieldtype": "Date", "width": 100},
		{"fieldname": "to_date", "label": _("To"), "fieldtype": "Date", "width": 100},
		{"fieldname": "number_of_days", "label": _("Days"), "fieldtype": "Int", "width": 70},
		{"fieldname": "reason", "label": _("Reason"), "fieldtype": "Data", "width": 150},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
		{
			"fieldname": "applied_by",
			"label": _("Applied By"),
			"fieldtype": "Link",
			"options": "User",
			"width": 160,
		},
		{"fieldname": "applied_on", "label": _("Applied On"), "fieldtype": "Datetime", "width": 150},
		{
			"fieldname": "approver",
			"label": _("Decided By"),
			"fieldtype": "Link",
			"options": "User",
			"width": 160,
		},
		{"fieldname": "decided_on", "label": _("Decided On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "approval_remarks", "label": _("Remarks"), "fieldtype": "Data", "width": 200},
	]


def get_chart(data):
	approved = {}
	for row in data:
		if row.status == "Approved":
			approved[row.reason] = approved.get(row.reason, 0) + cint(row.number_of_days)
	if not approved:
		return None
	reasons = sorted(approved, key=lambda r: -approved[r])
	return {
		"data": {
			"labels": [_(r) for r in reasons],
			"datasets": [{"name": _("Approved Days"), "values": [approved[r] for r in reasons]}],
		},
		"type": "bar",
		"colors": ["#2f9e44"],
	}


def get_summary(data):
	pending = sum(1 for row in data if row.status == "Pending")
	approved_days = sum(cint(row.number_of_days) for row in data if row.status == "Approved")
	rejected = sum(1 for row in data if row.status == "Rejected")
	return [
		{"label": _("Pending"), "value": pending, "datatype": "Int", "indicator": "Orange"},
		{"label": _("Approved Days"), "value": approved_days, "datatype": "Int", "indicator": "Green"},
		{"label": _("Rejected"), "value": rejected, "datatype": "Int", "indicator": "Red"},
	]
