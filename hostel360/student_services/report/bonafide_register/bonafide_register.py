# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from collections import Counter

import frappe
from frappe import _
from frappe.utils import add_days, add_months, getdate, nowdate

CERTIFIED_STATUSES = ("Approved", "Issued")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.from_date or add_months(nowdate(), -1))
	to_date = getdate(filters.to_date or nowdate())
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	rows = get_rows(filters, from_date, to_date)
	return get_columns(), rows, None, None, get_summary(rows)


def get_rows(filters, from_date, to_date):
	conditions = [
		["creation", ">=", f"{from_date} 00:00:00"],
		["creation", "<", f"{add_days(to_date, 1)} 00:00:00"],
	]
	for fieldname in ("hostel", "status", "purpose", "student"):
		if filters.get(fieldname):
			conditions.append([fieldname, "=", filters.get(fieldname)])
	rows = frappe.get_list(
		"Bonafide Certificate Request",
		filters=conditions,
		fields=[
			"name",
			"creation",
			"student",
			"student_name",
			"program",
			"year_of_study",
			"hostel",
			"purpose",
			"purpose_details",
			"status",
			"certificate_number",
			"issue_date",
			"valid_till",
			"approved_by",
			"approved_on",
			"rejection_reason",
			"issued_on",
		],
		order_by="creation desc",
		limit_page_length=0,
	)
	today = getdate(nowdate())
	for row in rows:
		row["requested_on"] = getdate(row.creation)
		row["validity"] = ""
		if row.status in CERTIFIED_STATUSES and row.valid_till:
			row["validity"] = _("Expired") if getdate(row.valid_till) < today else _("Valid")
	return rows


def get_columns():
	return [
		{"fieldname": "name", "label": _("Request"), "fieldtype": "Link", "options": "Bonafide Certificate Request", "width": 150},
		{"fieldname": "requested_on", "label": _("Requested On"), "fieldtype": "Date", "width": 110},
		{"fieldname": "student", "label": _("Student"), "fieldtype": "Link", "options": "Hostel Student", "width": 120},
		{"fieldname": "student_name", "label": _("Student Name"), "fieldtype": "Data", "width": 170},
		{"fieldname": "program", "label": _("Program"), "fieldtype": "Link", "options": "Academic Program", "width": 150},
		{"fieldname": "year_of_study", "label": _("Year"), "fieldtype": "Data", "width": 60},
		{"fieldname": "hostel", "label": _("Hostel"), "fieldtype": "Link", "options": "Hostel", "width": 90},
		{"fieldname": "purpose", "label": _("Purpose"), "fieldtype": "Data", "width": 120},
		{"fieldname": "purpose_details", "label": _("Purpose Details"), "fieldtype": "Data", "width": 180},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "certificate_number", "label": _("Certificate No."), "fieldtype": "Data", "width": 130},
		{"fieldname": "issue_date", "label": _("Issue Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "valid_till", "label": _("Valid Till"), "fieldtype": "Date", "width": 100},
		{"fieldname": "validity", "label": _("Validity"), "fieldtype": "Data", "width": 80},
		{"fieldname": "approved_by", "label": _("Decided By"), "fieldtype": "Link", "options": "User", "width": 150},
		{"fieldname": "approved_on", "label": _("Decided On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "rejection_reason", "label": _("Rejection Reason"), "fieldtype": "Data", "width": 180},
		{"fieldname": "issued_on", "label": _("Handed Over On"), "fieldtype": "Datetime", "width": 150},
	]


def get_summary(rows):
	counts = Counter(row.status for row in rows)
	return [
		{"label": _("Requests"), "value": len(rows), "datatype": "Int", "indicator": "Blue"},
		{"label": _("Pending"), "value": counts["Pending"], "datatype": "Int", "indicator": "Orange"},
		{
			"label": _("Certificates Issued"),
			"value": sum(counts[s] for s in CERTIFIED_STATUSES),
			"datatype": "Int",
			"indicator": "Green",
		},
		{"label": _("Rejected"), "value": counts["Rejected"], "datatype": "Int", "indicator": "Red"},
	]
