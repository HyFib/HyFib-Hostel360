# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, getdate, nowdate

from hostel360.hostel_operations.metrics import compute_uptime, daily_uptime

UPTIME_TARGET = 99.9
MAX_DAYS = 400


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.from_date or add_days(nowdate(), -30))
	to_date = getdate(filters.to_date or nowdate())
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	if (to_date - from_date).days > MAX_DAYS:
		frappe.throw(_("Choose a period of at most {0} days.").format(MAX_DAYS))

	incidents = _incidents_by_day(from_date, to_date)
	data = []
	for day in daily_uptime(from_date, to_date):
		counts = incidents.get(day["date"], {})
		data.append(
			{
				"date": day["date"],
				"uptime_percent": day["uptime_percent"],
				"downtime_minutes": day["downtime_minutes"],
				"samples": day["samples"],
				"measured_by": day["source"],
				"critical_alerts": counts.get("critical", 0),
				"outages": counts.get("outages", 0),
			}
		)
	overall = compute_uptime(get_datetime(from_date), get_datetime(add_days(to_date, 1)))
	return get_columns(), data, None, get_chart(data), get_summary(data, overall)


def get_columns():
	return [
		{"fieldname": "date", "label": _("Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "uptime_percent", "label": _("Uptime (%)"), "fieldtype": "Percent", "precision": 3, "width": 110},
		{"fieldname": "downtime_minutes", "label": _("Downtime (Minutes)"), "fieldtype": "Int", "width": 140},
		{"fieldname": "critical_alerts", "label": _("Critical Alerts"), "fieldtype": "Int", "width": 120},
		{"fieldname": "outages", "label": _("Outages"), "fieldtype": "Int", "width": 90},
		{"fieldname": "samples", "label": _("Samples"), "fieldtype": "Int", "width": 90},
		{"fieldname": "measured_by", "label": _("Measured By"), "fieldtype": "Data", "width": 220},
	]


def _incidents_by_day(from_date, to_date):
	alerts = frappe.get_list(
		"Ops Alert",
		filters=[
			["severity", "=", "Critical"],
			["first_seen", ">=", get_datetime(from_date)],
			["first_seen", "<", get_datetime(add_days(to_date, 1))],
		],
		fields=["first_seen", "is_outage"],
		limit_page_length=0,
	)
	by_day = {}
	for alert in alerts:
		day = getdate(alert.first_seen)
		counts = by_day.setdefault(day, {"critical": 0, "outages": 0})
		counts["critical"] += 1
		counts["outages"] += 1 if alert.is_outage else 0
	return by_day


def get_chart(data):
	measured = [row for row in data if row["uptime_percent"] is not None]
	if not measured:
		return None
	return {
		"data": {
			"labels": [frappe.format(row["date"], {"fieldtype": "Date"}) for row in data],
			"datasets": [
				{
					"name": _("Uptime (%)"),
					"values": [flt(row["uptime_percent"], 3) if row["uptime_percent"] is not None else 0 for row in data],
				},
				{"name": _("Target"), "values": [UPTIME_TARGET] * len(data)},
			],
		},
		"type": "line",
		"lineOptions": {"regionFill": 0, "hideDots": 1},
		"axisOptions": {"xIsSeries": 1},
		"colors": ["#2e7d32", "#9e9e9e"],
		"height": 260,
	}


def get_summary(data, overall):
	uptime = overall["uptime_percent"]
	met = uptime is not None and uptime >= UPTIME_TARGET
	return [
		{
			"value": flt(uptime, 3) if uptime is not None else _("No data"),
			"label": _("Uptime for the Period (%)"),
			"datatype": "Float" if uptime is not None else "Data",
			"indicator": "Green" if met else "Red",
		},
		{"value": UPTIME_TARGET, "label": _("Contracted Target (%)"), "datatype": "Float", "indicator": "Blue"},
		{
			"value": overall["downtime_minutes"],
			"label": _("Downtime (Minutes)"),
			"datatype": "Int",
			"indicator": "Green" if not overall["downtime_minutes"] else "Orange",
		},
		{
			"value": sum(row["critical_alerts"] for row in data),
			"label": _("Critical Incidents"),
			"datatype": "Int",
			"indicator": "Blue",
		},
	]
