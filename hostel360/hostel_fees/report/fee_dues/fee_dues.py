# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Outstanding dues with ageing by days past the due date (0-30, 31-60, 61-90, 90+).

Amounts are the current outstanding balances of invoices raised on or before the "as on" date.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, nowdate

from hostel360.hostel_fees.constants import INVOICE
from hostel360.hostel_fees.utils import get_permitted_hostels, money

# Labels are translated when the report runs, in the reader's language.
BUCKETS = (
	("not_due", "Not Yet Due"),
	("range_0_30", "0-30 Days"),
	("range_31_60", "31-60 Days"),
	("range_61_90", "61-90 Days"),
	("range_90_plus", "Above 90 Days"),
)


def bucket_for(days_past_due: int) -> str:
	if days_past_due < 0:
		return "not_due"
	if days_past_due <= 30:
		return "range_0_30"
	if days_past_due <= 60:
		return "range_31_60"
	if days_past_due <= 90:
		return "range_61_90"
	return "range_90_plus"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	as_on = getdate(filters.as_on_date or nowdate())
	invoices = get_invoices(filters, as_on)

	rows = []
	for invoice in invoices:
		days = date_diff(as_on, invoice.due_date)
		row = frappe._dict(invoice)
		row.days_past_due = max(days, 0)
		for key, _label in BUCKETS:
			row[key] = 0.0
		row[bucket_for(days)] = money(invoice.outstanding_amount)
		rows.append(row)

	if filters.group_by == "Student":
		data, columns = group_by_student(rows), student_columns()
	else:
		data, columns = rows, invoice_columns()
	return columns, data, None, get_chart(rows), get_summary(rows)


def get_invoices(filters, as_on):
	invoice = frappe.qb.DocType(INVOICE)
	query = (
		frappe.qb.from_(invoice)
		.select(
			invoice.name.as_("invoice"),
			invoice.student,
			invoice.student_name,
			invoice.hostel,
			invoice.invoice_type,
			invoice.academic_year,
			invoice.posting_date,
			invoice.due_date,
			invoice.total_amount,
			invoice.paid_amount,
			invoice.outstanding_amount,
		)
		.where(invoice.docstatus == 1)
		.where(invoice.outstanding_amount > 0)
		.where(invoice.posting_date <= as_on)
		.orderby(invoice.due_date)
		.orderby(invoice.student)
	)
	for fieldname in ("hostel", "invoice_type", "academic_year", "student"):
		if filters.get(fieldname):
			query = query.where(getattr(invoice, fieldname) == filters.get(fieldname))
	if filters.overdue_only:
		query = query.where(invoice.due_date < as_on)
	permitted = get_permitted_hostels(INVOICE)
	if permitted:
		query = query.where(invoice.hostel.isin(permitted))
	return query.run(as_dict=True)


def group_by_student(rows):
	students = {}
	for row in rows:
		entry = students.get(row.student)
		if not entry:
			entry = students[row.student] = frappe._dict(
				student=row.student,
				student_name=row.student_name,
				hostel=row.hostel,
				invoices=0,
				oldest_due_date=row.due_date,
				days_past_due=0,
				outstanding_amount=0.0,
				**{key: 0.0 for key, _label in BUCKETS},
			)
		entry.invoices += 1
		entry.outstanding_amount = money(entry.outstanding_amount + flt(row.outstanding_amount))
		entry.days_past_due = max(entry.days_past_due, row.days_past_due)
		if getdate(row.due_date) < getdate(entry.oldest_due_date):
			entry.oldest_due_date = row.due_date
		for key, _label in BUCKETS:
			entry[key] = money(entry[key] + flt(row[key]))
	return sorted(students.values(), key=lambda entry: (-entry.outstanding_amount, entry.student))


def bucket_columns():
	return [
		{"fieldname": key, "label": _(label), "fieldtype": "Currency", "width": 120} for key, label in BUCKETS
	]


def invoice_columns():
	return [
		{
			"fieldname": "invoice",
			"label": _("Invoice"),
			"fieldtype": "Link",
			"options": INVOICE,
			"width": 150,
		},
		{
			"fieldname": "student",
			"label": _("Student"),
			"fieldtype": "Link",
			"options": "Hostel Student",
			"width": 120,
		},
		{"fieldname": "student_name", "label": _("Student Name"), "fieldtype": "Data", "width": 170},
		{"fieldname": "hostel", "label": _("Hostel"), "fieldtype": "Link", "options": "Hostel", "width": 90},
		{"fieldname": "invoice_type", "label": _("Type"), "fieldtype": "Data", "width": 100},
		{"fieldname": "posting_date", "label": _("Invoice Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "due_date", "label": _("Due Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "days_past_due", "label": _("Days Overdue"), "fieldtype": "Int", "width": 90},
		{"fieldname": "total_amount", "label": _("Invoice Total"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "paid_amount", "label": _("Paid"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "outstanding_amount", "label": _("Outstanding"), "fieldtype": "Currency", "width": 120},
		*bucket_columns(),
	]


def student_columns():
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
		{"fieldname": "invoices", "label": _("Open Invoices"), "fieldtype": "Int", "width": 100},
		{"fieldname": "oldest_due_date", "label": _("Oldest Due Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "days_past_due", "label": _("Days Overdue"), "fieldtype": "Int", "width": 90},
		{"fieldname": "outstanding_amount", "label": _("Outstanding"), "fieldtype": "Currency", "width": 130},
		*bucket_columns(),
	]


def get_chart(rows):
	if not rows:
		return None
	return {
		"data": {
			"labels": [_(label) for _key, label in BUCKETS],
			"datasets": [
				{
					"name": _("Outstanding"),
					"values": [money(sum(flt(row[key]) for row in rows)) for key, _label in BUCKETS],
				}
			],
		},
		"type": "bar",
		"fieldtype": "Currency",
		"colors": ["#b42318"],
	}


def get_summary(rows):
	outstanding = money(sum(flt(row.outstanding_amount) for row in rows))
	not_due = money(sum(flt(row.not_due) for row in rows))
	return [
		{"value": outstanding, "label": _("Total Outstanding"), "datatype": "Currency", "indicator": "Red"},
		{
			"value": money(outstanding - not_due),
			"label": _("Overdue"),
			"datatype": "Currency",
			"indicator": "Red",
		},
		{"value": not_due, "label": _("Not Yet Due"), "datatype": "Currency", "indicator": "Orange"},
		{
			"value": len({row.student for row in rows}),
			"label": _("Students with Dues"),
			"datatype": "Int",
			"indicator": "Blue",
		},
		{"value": len(rows), "label": _("Open Invoices"), "datatype": "Int", "indicator": "Gray"},
	]
