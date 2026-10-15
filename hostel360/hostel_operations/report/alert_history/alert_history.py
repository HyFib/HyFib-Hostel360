# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, getdate, nowdate

SEVERITY_COLOURS = {"Critical": "#e24c4c", "Warning": "#f5a623", "Info": "#4a90e2"}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.from_date or add_days(nowdate(), -30))
	to_date = getdate(filters.to_date or nowdate())
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))

	rows = get_alerts(filters, from_date, to_date)
	data = [_row(alert) for alert in rows]
	return get_columns(), data, None, get_chart(data, from_date, to_date), get_summary(data)


def get_columns():
	return [
		{"fieldname": "alert", "label": _("Alert"), "fieldtype": "Link", "options": "Ops Alert", "width": 150},
		{"fieldname": "title", "label": _("Title"), "fieldtype": "Data", "width": 280},
		{"fieldname": "severity", "label": _("Severity"), "fieldtype": "Data", "width": 90},
		{"fieldname": "category", "label": _("Category"), "fieldtype": "Data", "width": 110},
		{"fieldname": "source", "label": _("Source"), "fieldtype": "Data", "width": 120},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
		{"fieldname": "first_seen", "label": _("First Seen"), "fieldtype": "Datetime", "width": 160},
		{"fieldname": "acknowledged_on", "label": _("Acknowledged"), "fieldtype": "Datetime", "width": 160},
		{"fieldname": "resolved_on", "label": _("Resolved"), "fieldtype": "Datetime", "width": 160},
		{"fieldname": "minutes_to_acknowledge", "label": _("Minutes to Acknowledge"), "fieldtype": "Float", "precision": 1, "width": 120},
		{"fieldname": "minutes_to_resolve", "label": _("Minutes to Resolve"), "fieldtype": "Float", "precision": 1, "width": 120},
		{"fieldname": "occurrence_count", "label": _("Occurrences"), "fieldtype": "Int", "width": 100},
		{"fieldname": "escalation_level", "label": _("Escalation Level"), "fieldtype": "Int", "width": 100},
		{"fieldname": "is_outage", "label": _("Outage"), "fieldtype": "Check", "width": 80},
	]


def get_alerts(filters, from_date, to_date):
	conditions = [
		["first_seen", ">=", get_datetime(from_date)],
		["first_seen", "<", get_datetime(add_days(to_date, 1))],
	]
	for fieldname in ("severity", "category", "status", "source"):
		if filters.get(fieldname):
			conditions.append([fieldname, "=", filters.get(fieldname)])
	if filters.get("outages_only"):
		conditions.append(["is_outage", "=", 1])
	return frappe.get_list(
		"Ops Alert",
		filters=conditions,
		fields=[
			"name",
			"title",
			"severity",
			"category",
			"source",
			"status",
			"first_seen",
			"acknowledged_on",
			"resolved_on",
			"occurrence_count",
			"escalation_level",
			"is_outage",
		],
		order_by="first_seen desc",
		limit_page_length=0,
	)


def _minutes(start, end):
	if not start or not end:
		return None
	return flt((get_datetime(end) - get_datetime(start)).total_seconds() / 60, 1)


def _row(alert):
	return {
		"alert": alert.name,
		"title": alert.title,
		"severity": alert.severity,
		"category": alert.category,
		"source": alert.source,
		"status": alert.status,
		"first_seen": alert.first_seen,
		"acknowledged_on": alert.acknowledged_on,
		"resolved_on": alert.resolved_on,
		"minutes_to_acknowledge": _minutes(alert.first_seen, alert.acknowledged_on),
		"minutes_to_resolve": _minutes(alert.first_seen, alert.resolved_on),
		"occurrence_count": alert.occurrence_count,
		"escalation_level": alert.escalation_level,
		"is_outage": alert.is_outage,
	}


def _mean(values):
	values = [v for v in values if v is not None]
	return flt(sum(values) / len(values), 1) if values else 0


def get_summary(data):
	critical = sum(1 for row in data if row["severity"] == "Critical")
	open_now = sum(1 for row in data if row["status"] in ("Open", "Acknowledged"))
	return [
		{"value": len(data), "label": _("Alerts"), "datatype": "Int", "indicator": "Blue"},
		{"value": critical, "label": _("Critical"), "datatype": "Int", "indicator": "Red" if critical else "Green"},
		{"value": open_now, "label": _("Still Open"), "datatype": "Int", "indicator": "Orange" if open_now else "Green"},
		{
			"value": _mean(row["minutes_to_acknowledge"] for row in data),
			"label": _("Mean Time to Acknowledge (min)"),
			"datatype": "Float",
			"indicator": "Blue",
		},
		{
			"value": _mean(row["minutes_to_resolve"] for row in data),
			"label": _("Mean Time to Resolve (min)"),
			"datatype": "Float",
			"indicator": "Blue",
		},
	]


def get_chart(data, from_date, to_date):
	days = (to_date - from_date).days + 1
	if not data or days > 120:
		return None
	labels = [add_days(from_date, offset) for offset in range(days)]
	index = {day: i for i, day in enumerate(labels)}
	datasets = []
	for severity in ("Critical", "Warning", "Info"):
		values = [0] * days
		for row in data:
			if row["severity"] == severity:
				values[index[getdate(row["first_seen"])]] += 1
		datasets.append({"name": _(severity), "values": values})
	return {
		"data": {"labels": [frappe.format(day, {"fieldtype": "Date"}) for day in labels], "datasets": datasets},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"colors": [SEVERITY_COLOURS["Critical"], SEVERITY_COLOURS["Warning"], SEVERITY_COLOURS["Info"]],
		"height": 240,
	}
