# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Monthly mess cost, student-days and the dividing-system daily rate."""

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, get_first_day, get_last_day, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.mess:
		frappe.throw(_("Select a mess."))
	from_date = getdate(filters.from_date or add_months(get_first_day(nowdate()), -11))
	to_date = getdate(filters.to_date or get_last_day(nowdate()))
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))

	values = {"mess": filters.mess, "from_date": from_date, "to_date": to_date}
	provisions = monthly(
		"""
		select date_format(posting_date, '%%Y-%%m') as month, sum(-stock_value_difference) as amount
		from `tabMess Stock Ledger Entry`
		where mess = %(mess)s and is_cancelled = 0 and voucher_type = 'Mess Stock Entry' and entry_type = 'Issue'
			and posting_date between %(from_date)s and %(to_date)s
		group by month
		""",
		values,
	)
	expenses = monthly(
		"""
		select date_format(posting_date, '%%Y-%%m') as month, sum(amount) as amount
		from `tabMess Expense`
		where mess = %(mess)s and docstatus = 1 and posting_date between %(from_date)s and %(to_date)s
		group by month
		""",
		values,
	)
	runs = frappe.db.sql(
		"""
		select name, date_format(from_date, '%%Y-%%m') as month, other_credits, total_cost,
			total_student_days, daily_rate, total_billed, bills_count
		from `tabMess Bill Run`
		where mess = %(mess)s and docstatus = 1 and from_date between %(from_date)s and %(to_date)s
		order by from_date asc
		""",
		values,
		as_dict=True,
	)
	runs_by_month = {}
	for run in runs:
		runs_by_month.setdefault(run.month, []).append(run)

	data = []
	cursor = from_date.replace(day=1)
	while cursor <= to_date:
		key = cursor.strftime("%Y-%m")
		month_runs = runs_by_month.get(key, [])
		credits = sum(flt(r.other_credits) for r in month_runs)
		student_days = sum(cint(r.total_student_days) for r in month_runs)
		billed_cost = sum(flt(r.total_cost) for r in month_runs)
		row = {
			"month": cursor.strftime("%b %Y"),
			"provision_cost": flt(provisions.get(key), 2),
			"other_expenses": flt(expenses.get(key), 2),
			"other_credits": flt(credits, 2),
			"student_days": student_days or None,
			"daily_rate": flt(billed_cost / student_days, 2) if student_days else None,
			"total_billed": flt(sum(flt(r.total_billed) for r in month_runs), 2) or None,
			"bills": sum(cint(r.bills_count) for r in month_runs) or None,
			"bill_runs": ", ".join(r.name for r in month_runs),
		}
		row["total_cost"] = flt(row["provision_cost"] + row["other_expenses"] - row["other_credits"], 2)
		data.append(row)
		cursor = getdate(add_months(cursor, 1))

	return get_columns(), data, None, get_chart(data), get_summary(data)


def monthly(query: str, values: dict) -> dict:
	return {row.month: flt(row.amount) for row in frappe.db.sql(query, values, as_dict=True)}


def get_columns():
	return [
		{"fieldname": "month", "label": _("Month"), "fieldtype": "Data", "width": 100},
		{
			"fieldname": "provision_cost",
			"label": _("Provisions Issued"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{"fieldname": "other_expenses", "label": _("Other Expenses"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "other_credits", "label": _("Credits"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "total_cost", "label": _("Total Cost"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "student_days", "label": _("Student-Days"), "fieldtype": "Int", "width": 110},
		{"fieldname": "daily_rate", "label": _("Daily Rate"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "bills", "label": _("Bills"), "fieldtype": "Int", "width": 70},
		{"fieldname": "total_billed", "label": _("Total Billed"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "bill_runs", "label": _("Bill Runs"), "fieldtype": "Data", "width": 200},
	]


def get_chart(data):
	points = [row for row in data if row["daily_rate"]]
	if not points:
		return None
	return {
		"data": {
			"labels": [row["month"] for row in points],
			"datasets": [{"name": _("Daily Rate"), "values": [row["daily_rate"] for row in points]}],
		},
		"type": "line",
		"fieldtype": "Currency",
		"colors": ["#e8590c"],
		"lineOptions": {"regionFill": 1},
	}


def get_summary(data):
	total_cost = sum(flt(row["total_cost"]) for row in data)
	billed_rows = [row for row in data if row["student_days"]]
	student_days = sum(cint(row["student_days"]) for row in billed_rows)
	billed_cost = sum(flt(row["daily_rate"]) * cint(row["student_days"]) for row in billed_rows)
	return [
		{"label": _("Total Cost"), "value": total_cost, "datatype": "Currency", "indicator": "Blue"},
		{"label": _("Student-Days Billed"), "value": student_days, "datatype": "Int", "indicator": "Green"},
		{
			"label": _("Average Daily Rate"),
			"value": flt(billed_cost / student_days, 2) if student_days else 0,
			"datatype": "Currency",
			"indicator": "Orange",
		},
	]
