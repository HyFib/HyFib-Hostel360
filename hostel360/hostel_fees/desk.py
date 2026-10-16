"""Desk helpers for the fee counter: fetch a student's dues, record a receipt, send a reminder.

Every method checks DocType permissions of the signed-in staff member; list queries go through
``frappe.get_list`` so hostel-scoped staff only see their hostels.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from hostel360.hostel_fees.constants import INVOICE, OFFLINE_MODES, PAYMENT
from hostel360.hostel_fees.utils import money


@frappe.whitelist()
def get_outstanding_invoices(student: str) -> list[dict]:
	"""Open invoices of a student, oldest due first."""
	frappe.has_permission(INVOICE, "read", throw=True)
	return frappe.get_list(
		INVOICE,
		filters={"student": student, "docstatus": 1, "outstanding_amount": (">", 0)},
		fields=[
			"name",
			"invoice_type",
			"posting_date",
			"due_date",
			"total_amount",
			"paid_amount",
			"outstanding_amount",
			"status",
		],
		order_by="due_date asc, posting_date asc",
		limit=100,
	)


@frappe.whitelist(methods=["POST"])
def record_payment(
	student: str,
	mode_of_payment: str,
	allocations: list | str,
	payment_date: str | None = None,
	reference_no: str | None = None,
	reference_date: str | None = None,
	bank_name: str | None = None,
	remarks: str | None = None,
) -> dict:
	"""Create and submit a receipt for money received at the counter."""
	frappe.has_permission(PAYMENT, "create", throw=True)
	frappe.has_permission(PAYMENT, "submit", throw=True)
	if mode_of_payment not in OFFLINE_MODES:
		frappe.throw(_("Choose how the money was received: {0}.").format(", ".join(OFFLINE_MODES)))

	rows = frappe.parse_json(allocations) if isinstance(allocations, str) else allocations
	rows = [frappe._dict(row) for row in rows or [] if money(frappe._dict(row).get("allocated_amount")) > 0]
	if not rows:
		frappe.throw(_("Enter the amount received against at least one invoice."))

	payment = frappe.new_doc(PAYMENT)
	payment.update(
		{
			"student": student,
			"mode_of_payment": mode_of_payment,
			"payment_date": getdate(payment_date) if payment_date else nowdate(),
			"reference_no": reference_no,
			"reference_date": reference_date,
			"bank_name": bank_name,
			"remarks": remarks,
		}
	)
	for row in rows:
		payment.append(
			"allocations", {"invoice": row.invoice, "allocated_amount": money(row.allocated_amount)}
		)
	payment.insert()
	payment.submit()
	return {"payment": payment.name, "amount": payment.amount}


@frappe.whitelist(methods=["POST"])
def send_reminder(invoice: str) -> dict:
	"""Send a fee reminder for one invoice now (the daily job keeps its own schedule)."""
	from hostel360.hostel_fees.reminders import send_invoice_reminder

	doc = frappe.get_doc(INVOICE, invoice)
	doc.check_permission("write")
	if doc.docstatus != 1 or money(doc.outstanding_amount) <= 0:
		frappe.throw(_("Only unpaid invoices can be reminded."))
	send_invoice_reminder(doc)
	return {"ok": True}
