# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Online payment transactions against their receipts, with anything needing attention flagged."""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, getdate, now_datetime

from hostel360.hostel_fees.constants import (
	LIVE_TX_STATUSES,
	PAYMENT,
	TRANSACTION,
	TX_REFUNDED,
	TX_SUCCESS,
)
from hostel360.hostel_fees.utils import get_payment_settings, get_permitted_hostels, money


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("Select both From Date and To Date."))

	rows = get_rows(filters)
	expiry = cint(get_payment_settings().transaction_expiry_minutes) or 60
	stale_before = add_to_date(now_datetime(), minutes=-expiry)
	verify_before = add_to_date(now_datetime(), days=-1)

	data = []
	for row in rows:
		row.receipt_status = (
			{0: _("Draft"), 1: _("Submitted"), 2: _("Cancelled")}.get(row.receipt_docstatus)
			if row.payment
			else ""
		)
		row.issue = get_issue(row, stale_before, verify_before)
		if filters.only_issues and not row.issue:
			continue
		data.append(row)

	return get_columns(), data, None, None, get_summary(rows)


def get_rows(filters):
	tx = frappe.qb.DocType(TRANSACTION)
	pay = frappe.qb.DocType(PAYMENT)
	query = (
		frappe.qb.from_(tx)
		.left_join(pay)
		.on(pay.name == tx.payment)
		.select(
			tx.name.as_("transaction"),
			tx.creation,
			tx.student,
			tx.student_name,
			tx.hostel,
			tx.amount,
			tx.status,
			tx.test_mode,
			tx.gateway_order_id,
			tx.gateway_payment_id,
			tx.payment_method,
			tx.payment,
			tx.failure_reason,
			tx.completed_on,
			tx.reconciled_on,
			tx.refunded_amount,
			pay.amount.as_("receipt_amount"),
			pay.docstatus.as_("receipt_docstatus"),
		)
		.where(tx.creation >= get_datetime(f"{getdate(filters.from_date)} 00:00:00"))
		.where(tx.creation <= get_datetime(f"{getdate(filters.to_date)} 23:59:59.999999"))
		.orderby(tx.creation, order=frappe.qb.desc)
	)
	if filters.status:
		query = query.where(tx.status == filters.status)
	if filters.hostel:
		query = query.where(tx.hostel == filters.hostel)
	permitted = get_permitted_hostels(TRANSACTION)
	if permitted:
		query = query.where(tx.hostel.isin(permitted))
	return query.run(as_dict=True)


def get_issue(row, stale_before, verify_before) -> str:
	if row.status == TX_SUCCESS:
		if not row.payment or row.receipt_docstatus != 1:
			return _("Paid but no active receipt")
		if money(row.receipt_amount) != money(row.amount):
			return _("Receipt amount differs")
		if money(row.refunded_amount) > 0:
			return _("Gateway refunded {0}; receipt still shows the full amount").format(
				money(row.refunded_amount)
			)
		if not row.reconciled_on and get_datetime(row.creation) < verify_before:
			return _("Not yet verified with the gateway")
	elif row.status in LIVE_TX_STATUSES and get_datetime(row.creation) < stale_before:
		return _("Open beyond the expiry time")
	elif row.status == TX_REFUNDED and row.receipt_docstatus == 1:
		return _("Refunded but receipt still active")
	return ""


def get_columns():
	return [
		{
			"fieldname": "transaction",
			"label": _("Transaction"),
			"fieldtype": "Link",
			"options": TRANSACTION,
			"width": 160,
		},
		{"fieldname": "creation", "label": _("Started"), "fieldtype": "Datetime", "width": 150},
		{
			"fieldname": "student",
			"label": _("Student"),
			"fieldtype": "Link",
			"options": "Hostel Student",
			"width": 120,
		},
		{"fieldname": "student_name", "label": _("Student Name"), "fieldtype": "Data", "width": 160},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "issue", "label": _("Needs Attention"), "fieldtype": "Data", "width": 220},
		{
			"fieldname": "payment",
			"label": _("Receipt"),
			"fieldtype": "Link",
			"options": PAYMENT,
			"width": 150,
		},
		{"fieldname": "receipt_status", "label": _("Receipt Status"), "fieldtype": "Data", "width": 100},
		{"fieldname": "receipt_amount", "label": _("Receipt Amount"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "gateway_order_id", "label": _("Gateway Order"), "fieldtype": "Data", "width": 170},
		{"fieldname": "gateway_payment_id", "label": _("Gateway Payment"), "fieldtype": "Data", "width": 170},
		{"fieldname": "payment_method", "label": _("Method"), "fieldtype": "Data", "width": 90},
		{"fieldname": "completed_on", "label": _("Completed On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "reconciled_on", "label": _("Verified On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "refunded_amount", "label": _("Refunded"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "failure_reason", "label": _("Failure Reason"), "fieldtype": "Data", "width": 220},
		{"fieldname": "test_mode", "label": _("Test"), "fieldtype": "Check", "width": 60},
	]


def get_summary(rows):
	success = [row for row in rows if row.status == TX_SUCCESS]
	failed = [row for row in rows if row.status in ("Failed", "Expired")]
	open_rows = [row for row in rows if row.status in LIVE_TX_STATUSES]
	attempts = len(success) + len(failed)
	# Test-mode payments carry no real money.
	collected = money(
		sum(flt(row.amount) - flt(row.refunded_amount) for row in success if not cint(row.test_mode))
	)
	return [
		{
			"value": collected,
			"label": _("Collected Online"),
			"datatype": "Currency",
			"indicator": "Green",
		},
		{"value": len(success), "label": _("Successful"), "datatype": "Int", "indicator": "Green"},
		{"value": len(failed), "label": _("Failed or Expired"), "datatype": "Int", "indicator": "Red"},
		{"value": len(open_rows), "label": _("In Progress"), "datatype": "Int", "indicator": "Orange"},
		{
			"value": round(100.0 * len(success) / attempts, 1) if attempts else 0,
			"label": _("Success Rate %"),
			"datatype": "Float",
			"indicator": "Blue",
		},
	]
