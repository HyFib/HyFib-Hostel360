# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import re
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, nowdate

RATINGS = (
	("overall", "Overall"),
	("food_quality", "Food Quality"),
	("cleanliness", "Cleanliness"),
	("staff_behaviour", "Staff Behaviour"),
	("facilities", "Facilities"),
)
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_period = filters.to_period or getdate(nowdate()).strftime("%Y-%m")
	from_period = filters.from_period or getdate(add_months(nowdate(), -5)).strftime("%Y-%m")
	for period in (from_period, to_period):
		if not PERIOD_RE.match(period):
			frappe.throw(_("Periods must be written as YYYY-MM."))
	if from_period > to_period:
		frappe.throw(_("From Period cannot be after To Period."))

	feedback = get_feedback(filters, from_period, to_period)
	if filters.show_comments:
		return (
			get_detail_columns(),
			get_detail_rows(feedback),
			None,
			get_chart(feedback),
			get_summary(feedback),
		)
	return get_columns(), summarise(feedback), None, get_chart(feedback), get_summary(feedback)


def get_feedback(filters, from_period, to_period):
	conditions = [["period", ">=", from_period], ["period", "<=", to_period]]
	for fieldname in ("hostel", "feedback_type"):
		if filters.get(fieldname):
			conditions.append([fieldname, "=", filters.get(fieldname)])
	return frappe.get_list(
		"Hostel Feedback",
		filters=conditions,
		fields=[
			"name",
			"period",
			"feedback_type",
			"hostel",
			"is_anonymous",
			"student_name",
			"comments",
			"action_taken",
			*[f for f, _label in RATINGS],
		],
		order_by="period desc, creation desc",
		limit_page_length=0,
	)


def stars(value):
	return round(flt(value) * 5, 1)


def average_stars(values):
	rated = [flt(v) for v in values if flt(v)]
	return flt(sum(rated) * 5 / len(rated), 1) if rated else None


def summarise(feedback):
	groups = defaultdict(list)
	for row in feedback:
		groups[(row.period, row.feedback_type, row.hostel or "")].append(row)
	rows = []
	for (period, feedback_type, hostel), items in sorted(groups.items(), key=lambda g: g[0], reverse=True):
		rows.append(
			{
				"period": period,
				"feedback_type": feedback_type,
				"hostel": hostel,
				"responses": len(items),
				"anonymous": sum(1 for r in items if r.is_anonymous),
				"with_comments": sum(1 for r in items if r.comments),
				**{f"avg_{field}": average_stars(r.get(field) for r in items) for field, _label in RATINGS},
			}
		)
	return rows


def get_detail_rows(feedback):
	return [
		{
			"name": row.name,
			"period": row.period,
			"feedback_type": row.feedback_type,
			"hostel": row.hostel,
			# Anonymous feedback never carries a student; say so plainly in exports.
			"student_name": _("Anonymous") if row.is_anonymous else row.student_name,
			**{field: stars(row.get(field)) if flt(row.get(field)) else None for field, _label in RATINGS},
			"comments": row.comments,
			"action_taken": row.action_taken,
		}
		for row in feedback
	]


def get_columns():
	return [
		{"fieldname": "period", "label": _("Period"), "fieldtype": "Data", "width": 90},
		{"fieldname": "feedback_type", "label": _("Type"), "fieldtype": "Data", "width": 100},
		{"fieldname": "hostel", "label": _("Hostel"), "fieldtype": "Link", "options": "Hostel", "width": 100},
		{"fieldname": "responses", "label": _("Responses"), "fieldtype": "Int", "width": 100},
		{"fieldname": "anonymous", "label": _("Anonymous"), "fieldtype": "Int", "width": 100},
		{"fieldname": "with_comments", "label": _("With Comments"), "fieldtype": "Int", "width": 120},
		*[
			{
				"fieldname": f"avg_{field}",
				"label": _("{0} (of 5)").format(_(label)),
				"fieldtype": "Float",
				"precision": 1,
				"width": 140,
			}
			for field, label in RATINGS
		],
	]


def get_detail_columns():
	return [
		{
			"fieldname": "name",
			"label": _("Feedback"),
			"fieldtype": "Link",
			"options": "Hostel Feedback",
			"width": 140,
		},
		{"fieldname": "period", "label": _("Period"), "fieldtype": "Data", "width": 90},
		{"fieldname": "feedback_type", "label": _("Type"), "fieldtype": "Data", "width": 100},
		{"fieldname": "hostel", "label": _("Hostel"), "fieldtype": "Link", "options": "Hostel", "width": 100},
		{"fieldname": "student_name", "label": _("Student"), "fieldtype": "Data", "width": 160},
		*[
			{"fieldname": field, "label": _(label), "fieldtype": "Float", "precision": 1, "width": 110}
			for field, label in RATINGS
		],
		{"fieldname": "comments", "label": _("Comments"), "fieldtype": "Data", "width": 300},
		{"fieldname": "action_taken", "label": _("Action Taken"), "fieldtype": "Data", "width": 220},
	]


def get_chart(feedback):
	if not feedback:
		return None
	by_period = defaultdict(list)
	for row in feedback:
		by_period[row.period].append(row.overall)
	periods = sorted(by_period)
	return {
		"data": {
			"labels": periods,
			"datasets": [
				{
					"name": _("Average Overall Rating"),
					"values": [average_stars(by_period[p]) or 0 for p in periods],
				}
			],
		},
		"type": "line",
		"lineOptions": {"regionFill": 1},
		"colors": ["#2490ef"],
		"axisOptions": {"xIsSeries": 1},
	}


def get_summary(feedback):
	overall = average_stars(r.overall for r in feedback)
	food = average_stars(r.food_quality for r in feedback)
	return [
		{"label": _("Responses"), "value": len(feedback), "datatype": "Int", "indicator": "Blue"},
		{
			"label": _("Average Overall (of 5)"),
			"value": overall or 0,
			"datatype": "Float",
			"indicator": "Green" if (overall or 0) >= 3.5 else "Orange",
		},
		{
			"label": _("Average Food Quality (of 5)"),
			"value": food or 0,
			"datatype": "Float",
			"indicator": "Blue",
		},
		{
			"label": _("Anonymous"),
			"value": sum(1 for r in feedback if r.is_anonymous),
			"datatype": "Int",
			"indicator": "Gray",
		},
	]
