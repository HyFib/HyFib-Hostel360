# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Day-end counter summary: receipts per collector and mode, plus receipts cancelled that day.

Used to close the cash counter: the Cash row of each collector is the cash they should hand over.

A receipt counts as collected on its payment date, and a cancellation on the day the receipt was
cancelled. So today's summary shows a cancellation of yesterday's receipt next to today's
collections, and re-running a past day still shows what was handed over when that counter
closed, even if some of its receipts were cancelled later. Test-mode gateway receipts (no money
received) are left out.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce, Date
from frappe.utils import flt, getdate, nowdate

from hostel360.hostel_fees.constants import ONLINE, PAYMENT
from hostel360.hostel_fees.utils import get_permitted_hostels, money


def execute(filters=None):
	filters = frappe._dict(filters or {})
	day = getdate(filters.date or nowdate())
	receipts = get_receipts(day, filters)

	groups = {}
	for receipt in receipts:
		collector = (
			_("Online (gateway)")
			if receipt.mode_of_payment == ONLINE
			else (receipt.received_by or _("Unknown"))
		)
		key = (collector, receipt.mode_of_payment)
		group = groups.setdefault(
			key,
			frappe._dict(
				collector=collector,
				mode_of_payment=receipt.mode_of_payment,
				receipts=0,
				amount=0.0,
				cancelled_receipts=0,
				cancelled_amount=0.0,
				first_receipt=None,
				last_receipt=None,
			),
		)
		if not receipt.cancelled_that_day:
			group.receipts += 1
			group.amount = money(group.amount + flt(receipt.amount))
			group.first_receipt = group.first_receipt or receipt.name
			group.last_receipt = receipt.name
		else:
			group.cancelled_receipts += 1
			group.cancelled_amount = money(group.cancelled_amount + flt(receipt.amount))

	data = sorted(groups.values(), key=lambda g: (g.collector, g.mode_of_payment))
	return get_columns(), data, None, get_chart(data), get_summary(data)


def get_receipts(day, filters):
	"""Receipts collected on ``day`` (even if cancelled on a later day) and receipts cancelled on ``day``."""
	payment = frappe.qb.DocType(PAYMENT)
	# Receipts cancelled before cancellations were time-stamped fall back to their last change,
	# which Frappe sets when a document is cancelled.
	cancelled_on = Coalesce(payment.cancelled_on, payment.modified)

	def query(*conditions):
		q = (
			frappe.qb.from_(payment)
			.select(
				payment.name,
				payment.docstatus,
				payment.mode_of_payment,
				payment.amount,
				payment.received_by,
			)
			.where(payment.is_test == 0)
			.orderby(payment.name)
		)
		for condition in conditions:
			q = q.where(condition)
		if filters.hostel:
			q = q.where(payment.hostel == filters.hostel)
		if filters.received_by:
			q = q.where(payment.received_by == filters.received_by)
		permitted = get_permitted_hostels(PAYMENT)
		if permitted:
			q = q.where(payment.hostel.isin(permitted))
		return q.run(as_dict=True)

	collected = query(
		payment.payment_date == day,
		(payment.docstatus == 1) | ((payment.docstatus == 2) & (Date(cancelled_on) > day)),
	)
	cancelled = query(payment.docstatus == 2, Date(cancelled_on) == day)
	for row in collected:
		row.cancelled_that_day = False
	for row in cancelled:
		row.cancelled_that_day = True
	return collected + cancelled


def get_columns():
	return [
		{"fieldname": "collector", "label": _("Collected By"), "fieldtype": "Data", "width": 200},
		{"fieldname": "mode_of_payment", "label": _("Mode"), "fieldtype": "Data", "width": 120},
		{"fieldname": "receipts", "label": _("Receipts"), "fieldtype": "Int", "width": 90},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 130},
		{
			"fieldname": "first_receipt",
			"label": _("First Receipt"),
			"fieldtype": "Link",
			"options": PAYMENT,
			"width": 150,
		},
		{
			"fieldname": "last_receipt",
			"label": _("Last Receipt"),
			"fieldtype": "Link",
			"options": PAYMENT,
			"width": 150,
		},
		{"fieldname": "cancelled_receipts", "label": _("Cancelled"), "fieldtype": "Int", "width": 90},
		{
			"fieldname": "cancelled_amount",
			"label": _("Cancelled Amount"),
			"fieldtype": "Currency",
			"width": 130,
		},
	]


def get_chart(data):
	by_mode = {}
	for row in data:
		by_mode[row.mode_of_payment] = money(by_mode.get(row.mode_of_payment, 0) + flt(row.amount))
	by_mode = {mode: amount for mode, amount in by_mode.items() if amount}
	if not by_mode:
		return None
	return {
		"data": {
			"labels": [_(mode) for mode in by_mode],
			"datasets": [{"name": _("Collected"), "values": list(by_mode.values())}],
		},
		"type": "donut",
		"fieldtype": "Currency",
	}


def get_summary(data):
	total = money(sum(flt(row.amount) for row in data))
	cash = money(sum(flt(row.amount) for row in data if row.mode_of_payment == "Cash"))
	online = money(sum(flt(row.amount) for row in data if row.mode_of_payment == ONLINE))
	return [
		{"value": total, "label": _("Total Collected"), "datatype": "Currency", "indicator": "Green"},
		{"value": cash, "label": _("Cash to Hand Over"), "datatype": "Currency", "indicator": "Orange"},
		{"value": online, "label": _("Online"), "datatype": "Currency", "indicator": "Blue"},
		{
			"value": sum(row.receipts for row in data),
			"label": _("Receipts"),
			"datatype": "Int",
			"indicator": "Gray",
		},
		{
			"value": sum(row.cancelled_receipts for row in data),
			"label": _("Cancelled Receipts"),
			"datatype": "Int",
			"indicator": "Red",
		},
		{
			"value": money(sum(flt(row.cancelled_amount) for row in data)),
			"label": _("Cancelled Amount"),
			"datatype": "Currency",
			"indicator": "Red",
		},
	]
