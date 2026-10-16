"""Daily fee housekeeping."""

import frappe
from frappe.utils import getdate, nowdate

from hostel360.hostel_fees.constants import INVOICE, OVERDUE, PARTLY_PAID, UNPAID


def mark_overdue_invoices() -> int:
	"""Flag submitted invoices with an outstanding balance past their due date as Overdue."""
	invoice = frappe.qb.DocType(INVOICE)
	today = getdate(nowdate())
	condition = (
		(invoice.docstatus == 1)
		& (invoice.outstanding_amount > 0)
		& (invoice.due_date < today)
		& (invoice.status.isin([UNPAID, PARTLY_PAID]))
	)
	names = frappe.qb.from_(invoice).select(invoice.name).where(condition).run(pluck=True)
	if names:
		frappe.qb.update(invoice).set(invoice.status, OVERDUE).where(condition).run()
		frappe.logger("hostel360").info(f"Marked {len(names)} fee invoices overdue")
	return len(names)
