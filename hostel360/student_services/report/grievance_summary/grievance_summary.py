# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_first_day, getdate, nowdate

STATUSES = ("Open", "In Progress", "Escalated", "Reopened", "Resolved", "Closed")
OPEN_STATUSES = ("Open", "In Progress", "Escalated", "Reopened")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.from_date or get_first_day(nowdate()))
	to_date = getdate(filters.to_date or nowdate())
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))

	grievances = get_grievances(filters, from_date, to_date)
	group_by = "hostel" if filters.group_by == "Hostel" else "category"
	rows = summarise(grievances, group_by)
	return get_columns(group_by), rows, None, get_chart(rows), get_summary(grievances)


def get_grievances(filters, from_date, to_date):
	conditions = [
		["creation", ">=", f"{from_date} 00:00:00"],
		["creation", "<", f"{add_days(to_date, 1)} 00:00:00"],
	]
	for fieldname in ("hostel", "category", "priority"):
		if filters.get(fieldname):
			conditions.append([fieldname, "=", filters.get(fieldname)])
	# get_list applies the confidentiality rules, so each user sees the totals they may see.
	return frappe.get_list(
		"Hostel Grievance",
		filters=conditions,
		fields=["name", "category", "hostel", "status", "priority", "resolution_hours", "sla_breached", "student_rating"],
		limit_page_length=0,
	)


def summarise(grievances, group_by):
	groups = defaultdict(lambda: {"total": 0, "hours": [], "ratings": [], "breaches": 0, **{s: 0 for s in STATUSES}})
	for row in grievances:
		group = groups[row.get(group_by) or _("Not Set")]
		group["total"] += 1
		group[row.status] = group.get(row.status, 0) + 1
		if row.status in ("Resolved", "Closed") and row.resolution_hours:
			group["hours"].append(flt(row.resolution_hours))
		if row.sla_breached:
			group["breaches"] += 1
		if flt(row.student_rating):
			group["ratings"].append(flt(row.student_rating) * 5)

	rows = []
	for key in sorted(groups, key=lambda k: -groups[k]["total"]):
		group = groups[key]
		rows.append(
			{
				"group": key,
				"total": group["total"],
				**{frappe.scrub(s): group[s] for s in STATUSES},
				"pending": sum(group[s] for s in OPEN_STATUSES),
				"avg_resolution_hours": average(group["hours"]),
				"sla_breaches": group["breaches"],
				"sla_breach_percent": flt(group["breaches"] * 100 / group["total"], 1) if group["total"] else 0,
				"avg_rating": average(group["ratings"]),
			}
		)
	return rows


def average(values):
	return flt(sum(values) / len(values), 1) if values else None


def get_columns(group_by):
	group_column = (
		{"fieldname": "group", "label": _("Hostel"), "fieldtype": "Link", "options": "Hostel", "width": 140}
		if group_by == "hostel"
		else {"fieldname": "group", "label": _("Category"), "fieldtype": "Data", "width": 170}
	)
	columns = [group_column, {"fieldname": "total", "label": _("Total"), "fieldtype": "Int", "width": 70}]
	columns += [{"fieldname": frappe.scrub(s), "label": _(s), "fieldtype": "Int", "width": 90} for s in STATUSES]
	columns += [
		{"fieldname": "pending", "label": _("Pending"), "fieldtype": "Int", "width": 80},
		{
			"fieldname": "avg_resolution_hours",
			"label": _("Avg Resolution (Hours)"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 150,
		},
		{"fieldname": "sla_breaches", "label": _("SLA Breaches"), "fieldtype": "Int", "width": 110},
		{"fieldname": "sla_breach_percent", "label": _("SLA Breach %"), "fieldtype": "Percent", "width": 110},
		{"fieldname": "avg_rating", "label": _("Avg Rating (of 5)"), "fieldtype": "Float", "precision": 1, "width": 130},
	]
	return columns


def get_chart(rows):
	if not rows:
		return None
	return {
		"data": {
			"labels": [row["group"] for row in rows],
			"datasets": [
				{"name": _("Pending"), "values": [row["pending"] for row in rows]},
				{"name": _("Resolved"), "values": [row["resolved"] for row in rows]},
				{"name": _("Closed"), "values": [row["closed"] for row in rows]},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"colors": ["#f8814f", "#29cd42", "#98a1a9"],
	}


def get_summary(grievances):
	total = len(grievances)
	pending = sum(1 for g in grievances if g.status in OPEN_STATUSES)
	escalated = sum(1 for g in grievances if g.status == "Escalated")
	breaches = sum(1 for g in grievances if g.sla_breached)
	hours = [flt(g.resolution_hours) for g in grievances if g.status in ("Resolved", "Closed") and g.resolution_hours]
	return [
		{"label": _("Grievances"), "value": total, "datatype": "Int", "indicator": "Blue"},
		{"label": _("Pending"), "value": pending, "datatype": "Int", "indicator": "Orange"},
		{"label": _("Escalated Now"), "value": escalated, "datatype": "Int", "indicator": "Red"},
		{"label": _("SLA Breaches"), "value": breaches, "datatype": "Int", "indicator": "Red" if breaches else "Green"},
		{"label": _("Avg Resolution (Hours)"), "value": average(hours) or 0, "datatype": "Float", "indicator": "Blue"},
	]
