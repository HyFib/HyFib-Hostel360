# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Collections in a period, by receipt, with the amount split across fee heads.

A receipt that pays part of an invoice is split across that invoice's fee heads in proportion to
their amounts, so the fee-head columns always add up to what was collected.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate

from hostel360.hostel_fees.constants import ALLOCATION, INVOICE, INVOICE_ITEM, ONLINE, PAYMENT
from hostel360.hostel_fees.utils import get_permitted_hostels, money


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	payments = get_payments(filters)
	breakdown = get_fee_head_breakdown([p.name for p in payments])
	heads = sorted({head for split in breakdown.values() for head in split})

	data = []
	for payment in payments:
		split = breakdown.get(payment.name, {})
		row = {
			"payment": payment.name,
			"payment_date": payment.payment_date,
			"student": payment.student,
			"student_name": payment.student_name,
			"hostel": payment.hostel,
			"mode_of_payment": payment.mode_of_payment,
			"reference_no": payment.reference_no,
			"received_by": payment.received_by,
			"unallocated_amount": money(payment.unallocated_amount),
			"amount": money(payment.amount),
		}
		for head in heads:
			row[frappe.scrub(head)] = money(split.get(head, 0))
		data.append(row)

	return get_columns(heads), data, None, get_chart(payments, filters), get_summary(payments)


def validate_filters(filters):
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("Select both From Date and To Date."))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date."))


def get_payments(filters):
	payment = frappe.qb.DocType(PAYMENT)
	query = (
		frappe.qb.from_(payment)
		.select(
			payment.name,
			payment.payment_date,
			payment.student,
			payment.student_name,
			payment.hostel,
			payment.mode_of_payment,
			payment.reference_no,
			payment.received_by,
			payment.amount,
			payment.unallocated_amount,
		)
		.where(payment.docstatus == 1)
		.where(payment.payment_date.between(filters.from_date, filters.to_date))
		.orderby(payment.payment_date)
		.orderby(payment.name)
	)
	if filters.hostel:
		query = query.where(payment.hostel == filters.hostel)
	if filters.mode_of_payment:
		query = query.where(payment.mode_of_payment == filters.mode_of_payment)
	if filters.student:
		query = query.where(payment.student == filters.student)
	permitted = get_permitted_hostels(PAYMENT)
	if permitted:
		query = query.where(payment.hostel.isin(permitted))
	return query.run(as_dict=True)


def get_fee_head_breakdown(payment_names: list[str]) -> dict:
	"""{payment: {fee_head: amount}}"""
	if not payment_names:
		return {}

	allocations = frappe.get_all(
		ALLOCATION,
		filters={"parent": ("in", payment_names), "parenttype": PAYMENT},
		fields=["parent", "invoice", "allocated_amount"],
	)
	invoices = list({row.invoice for row in allocations})
	items = {}
	if invoices:
		for item in frappe.get_all(
			INVOICE_ITEM,
			filters={"parent": ("in", invoices), "parenttype": INVOICE},
			fields=["parent", "fee_head", "amount"],
			order_by="idx asc",
		):
			items.setdefault(item.parent, []).append(item)

	breakdown = {}
	for allocation in allocations:
		split = breakdown.setdefault(allocation.parent, {})
		for head, amount in apportion(money(allocation.allocated_amount), items.get(allocation.invoice, [])):
			split[head] = money(split.get(head, 0) + amount)
	return breakdown


def apportion(amount: float, items: list) -> list[tuple[str, float]]:
	"""Split ``amount`` over invoice items pro rata; the last item absorbs rounding."""
	total = sum(flt(item.amount) for item in items)
	if not items or total <= 0:
		return [(_("Unclassified"), amount)]
	shares, assigned = [], 0.0
	for index, item in enumerate(items):
		if index == len(items) - 1:
			share = money(amount - assigned)
		else:
			share = money(amount * flt(item.amount) / total)
			assigned = money(assigned + share)
		shares.append((item.fee_head, share))
	return shares


def get_columns(heads: list[str]) -> list[dict]:
	columns = [
		{
			"fieldname": "payment",
			"label": _("Receipt"),
			"fieldtype": "Link",
			"options": PAYMENT,
			"width": 150,
		},
		{"fieldname": "payment_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "student",
			"label": _("Student"),
			"fieldtype": "Link",
			"options": "Hostel Student",
			"width": 120,
		},
		{"fieldname": "student_name", "label": _("Student Name"), "fieldtype": "Data", "width": 170},
		{"fieldname": "hostel", "label": _("Hostel"), "fieldtype": "Link", "options": "Hostel", "width": 90},
		{"fieldname": "mode_of_payment", "label": _("Mode"), "fieldtype": "Data", "width": 110},
		{"fieldname": "reference_no", "label": _("Reference"), "fieldtype": "Data", "width": 140},
		{
			"fieldname": "received_by",
			"label": _("Received By"),
			"fieldtype": "Link",
			"options": "User",
			"width": 140,
		},
	]
	columns += [
		{"fieldname": frappe.scrub(head), "label": _(head), "fieldtype": "Currency", "width": 130}
		for head in heads
	]
	columns += [
		{"fieldname": "unallocated_amount", "label": _("Unallocated"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 130},
	]
	return columns


def get_chart(payments, filters):
	if not payments:
		return None
	monthly = date_diff(filters.to_date, filters.from_date) > 62
	totals = {}
	for payment in payments:
		day = getdate(payment.payment_date)
		key = day.strftime("%Y-%m") if monthly else day.isoformat()
		totals[key] = money(totals.get(key, 0) + flt(payment.amount))
	labels = sorted(totals)
	return {
		"data": {
			"labels": labels,
			"datasets": [{"name": _("Collected"), "values": [totals[label] for label in labels]}],
		},
		"type": "bar",
		"fieldtype": "Currency",
		"colors": ["#1f4e79"],
	}


def get_summary(payments):
	total = money(sum(flt(p.amount) for p in payments))
	online = money(sum(flt(p.amount) for p in payments if p.mode_of_payment == ONLINE))
	cash = money(sum(flt(p.amount) for p in payments if p.mode_of_payment == "Cash"))
	return [
		{"value": total, "label": _("Total Collected"), "datatype": "Currency", "indicator": "Green"},
		{"value": len(payments), "label": _("Receipts"), "datatype": "Int", "indicator": "Blue"},
		{"value": online, "label": _("Online"), "datatype": "Currency", "indicator": "Blue"},
		{"value": cash, "label": _("Cash"), "datatype": "Currency", "indicator": "Gray"},
		{
			"value": money(total - online - cash),
			"label": _("UPI, DD, Cheque and Bank"),
			"datatype": "Currency",
			"indicator": "Gray",
		},
	]
