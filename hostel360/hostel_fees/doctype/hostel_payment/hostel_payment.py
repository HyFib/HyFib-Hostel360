# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, formatdate, getdate, now_datetime, nowdate

from hostel360.hostel_fees import notifications
from hostel360.hostel_fees.constants import CASH, INVOICE, ONLINE, TRANSACTION, TX_SUCCESS
from hostel360.hostel_fees.utils import (
	DISPLAY_DATE_FORMAT,
	assert_hostel_permitted,
	get_portal_link,
	get_student_print_details,
	is_migration,
	money,
	resolve_record_hostel,
)
from hostel360.setup.roles import ADMIN_ROLES
from hostel360.utils.context import has_any_role
from hostel360.utils.jinja import format_inr

# Allocation may exceed the outstanding amount by less than half a paisa (float noise).
TOLERANCE = 0.005


class HostelPayment(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from hostel360.hostel_fees.doctype.hostel_payment_allocation.hostel_payment_allocation import (
			HostelPaymentAllocation,
		)

		allocations: DF.Table[HostelPaymentAllocation]
		amended_from: DF.Link | None
		amount: DF.Currency
		bank_name: DF.Data | None
		cancelled_by: DF.Link | None
		cancelled_on: DF.Datetime | None
		hostel: DF.Link | None
		is_test: DF.Check
		mode_of_payment: DF.Literal["Online", "Cash", "UPI", "Demand Draft", "Cheque", "Bank Transfer"]
		naming_series: DF.Literal["RCPT-.YYYY.-.#####"]
		payment_date: DF.Date
		payment_transaction: DF.Link | None
		received_by: DF.Link | None
		reference_date: DF.Date | None
		reference_no: DF.Data | None
		remarks: DF.SmallText | None
		student: DF.Link
		student_name: DF.Data | None
		total_allocated: DF.Currency
		unallocated_amount: DF.Currency
	# end: auto-generated types

	@property
	def is_gateway_receipt(self) -> bool:
		return self.mode_of_payment == ONLINE and bool(self.payment_transaction)

	def validate(self):
		self.set_missing_values()
		assert_hostel_permitted(self)
		self.validate_mode()
		self.validate_dates()
		self.validate_allocations()
		self.check_invoice_access()
		self.calculate_totals()

	def set_missing_values(self):
		# Current hostel, else the one the student left: hostel-scoped staff are filtered on it.
		self.hostel = resolve_record_hostel(self)
		if self.reference_no:
			self.reference_no = self.reference_no.strip()
		if self.mode_of_payment != ONLINE and not self.received_by:
			self.received_by = frappe.session.user

	def validate_mode(self):
		if self.mode_of_payment == ONLINE:
			if is_migration() and not self.payment_transaction:
				self.is_test = 0
				return
			if not self.payment_transaction:
				frappe.throw(
					_(
						"Online receipts are issued automatically when a gateway payment succeeds. Choose the mode the money was received in."
					),
					title=_("Not an online payment"),
				)
			tx = frappe.db.get_value(
				TRANSACTION,
				self.payment_transaction,
				["student", "amount", "status", "payment", "gateway_payment_id", "test_mode"],
				as_dict=True,
			)
			if not tx or tx.student != self.student:
				frappe.throw(
					_("Online transaction {0} does not belong to this student.").format(
						self.payment_transaction
					)
				)
			# Whether money is real follows the transaction, never the request.
			self.is_test = cint(tx.test_mode)
			if money(tx.amount) != money(self.amount):
				frappe.throw(
					_("The receipt amount must equal the online transaction amount {0}.").format(
						format_inr(tx.amount)
					)
				)
			if not self.flags.from_gateway and tx.status != TX_SUCCESS:
				frappe.throw(_("Online transaction {0} has not succeeded.").format(self.payment_transaction))
			if tx.payment and tx.payment not in (self.name, self.amended_from):
				if frappe.db.get_value("Hostel Payment", tx.payment, "docstatus") == 1:
					frappe.throw(
						_("Online transaction {0} already has receipt {1}.").format(
							self.payment_transaction, tx.payment
						)
					)
			return

		self.is_test = 0
		if self.payment_transaction:
			frappe.throw(_("Only online receipts can be linked to an online transaction."))
		if self.mode_of_payment != CASH and not self.reference_no and not is_migration():
			frappe.throw(
				_("Enter the reference number for a {0} payment (UTR, DD or cheque number).").format(
					_(self.mode_of_payment)
				),
				frappe.MandatoryError,
			)

	def validate_dates(self):
		if not self.payment_date:
			self.payment_date = nowdate()
		if is_migration():
			return
		if getdate(self.payment_date) > getdate(nowdate()):
			frappe.throw(_("Payment date cannot be in the future."))

	def validate_allocations(self):
		if not self.allocations:
			if self.is_gateway_receipt:
				return
			frappe.throw(_("Allocate the payment to at least one invoice."), title=_("No invoices"))

		names = [row.invoice for row in self.allocations]
		duplicates = {name for name in names if names.count(name) > 1}
		if duplicates:
			frappe.throw(_("Invoice {0} is listed more than once.").format(", ".join(sorted(duplicates))))

		invoices = {
			row.name: row
			for row in frappe.get_all(
				INVOICE,
				filters={"name": ("in", names)},
				fields=["name", "student", "docstatus", "outstanding_amount", "invoice_type", "due_date"],
			)
		}
		for row in self.allocations:
			row.allocated_amount = money(row.allocated_amount)
			invoice = invoices.get(row.invoice)
			if not invoice or invoice.student != self.student:
				frappe.throw(
					_("Row {0}: invoice {1} does not belong to this student.").format(row.idx, row.invoice)
				)
			if invoice.docstatus != 1:
				frappe.throw(_("Row {0}: invoice {1} is not submitted.").format(row.idx, row.invoice))
			if row.allocated_amount <= 0:
				frappe.throw(_("Row {0}: allocated amount must be greater than zero.").format(row.idx))
			row.outstanding_before = money(invoice.outstanding_amount)
			row.invoice_type = invoice.invoice_type
			row.due_date = invoice.due_date
			if row.allocated_amount > money(invoice.outstanding_amount) + TOLERANCE:
				frappe.throw(
					_("Row {0}: {1} is more than the {2} outstanding on invoice {3}.").format(
						row.idx,
						format_inr(row.allocated_amount),
						format_inr(invoice.outstanding_amount),
						row.invoice,
					),
					title=_("Over-allocation"),
				)

	def check_invoice_access(self) -> None:
		"""A receipt saved or cancelled from the desk may only touch invoices its author can change.

		Allocation rows name invoices directly, and Frappe applies hostel User Permissions to the
		receipt's own hostel only. An invoice stays with the hostel that issued it after the
		student moves, so without this a hostel-scoped user could settle (or, by cancelling,
		reopen) another hostel's invoice. Gateway receipts and the migration are system work.
		"""
		if self.flags.ignore_permissions or is_migration() or not self.allocations:
			return
		names = list({row.invoice for row in self.allocations if row.invoice})
		hostels = dict(
			frappe.get_all(INVOICE, filters={"name": ("in", names)}, fields=["name", "hostel"], as_list=True)
		)
		for row in self.allocations:
			if row.invoice not in hostels or frappe.has_permission(INVOICE, "write", doc=row.invoice):
				continue
			frappe.throw(
				_("Row {0}: invoice {1} belongs to hostel {2}, which is not one of your assigned hostels.").format(
					row.idx, row.invoice, hostels[row.invoice] or _("(none)")
				),
				frappe.PermissionError,
				title=_("Not permitted"),
			)

	def calculate_totals(self):
		self.total_allocated = money(sum(money(row.allocated_amount) for row in self.allocations))
		if not money(self.amount):
			self.amount = self.total_allocated
		self.amount = money(self.amount)
		if self.amount <= 0:
			frappe.throw(_("Amount must be greater than zero."))

		self.unallocated_amount = money(self.amount - self.total_allocated)
		if self.unallocated_amount < 0:
			frappe.throw(
				_("Allocated amounts ({0}) exceed the amount received ({1}).").format(
					format_inr(self.total_allocated), format_inr(self.amount)
				)
			)
		if self.unallocated_amount > 0 and not self.is_gateway_receipt:
			frappe.throw(
				_("The amount received ({0}) must equal the total allocated to invoices ({1}).").format(
					format_inr(self.amount), format_inr(self.total_allocated)
				),
				title=_("Amount not fully allocated"),
			)

	def before_submit(self):
		# Lock the invoices and check again: another receipt may have been submitted since this
		# one was saved as a draft.
		from hostel360.hostel_fees.transactions import lock_invoices

		locked = lock_invoices([row.invoice for row in self.allocations])
		for row in self.allocations:
			invoice = locked.get(row.invoice)
			if not invoice or invoice.docstatus != 1 or invoice.student != self.student:
				frappe.throw(
					_("Row {0}: invoice {1} is no longer open for payment.").format(row.idx, row.invoice)
				)
			outstanding = money(invoice.outstanding_amount)
			if row.allocated_amount > outstanding + TOLERANCE:
				frappe.throw(
					_("Row {0}: only {1} is now outstanding on invoice {2}. Refresh the allocation.").format(
						row.idx, format_inr(outstanding), row.invoice
					),
					title=_("Over-allocation"),
				)
			row.outstanding_before = outstanding

	def on_submit(self):
		self.update_invoices()
		self.link_transaction()
		if self.is_test:
			# Nobody paid real money; a "payment received" message would mislead the family.
			return
		notifications.notify_student(
			notifications.EVENT_PAYMENT_RECEIVED,
			self.student,
			self.get_notification_context(),
			reference_doctype=self.doctype,
			reference_name=self.name,
		)

	def before_cancel(self):
		if self.is_gateway_receipt and not has_any_role(ADMIN_ROLES):
			frappe.throw(
				_(
					"Online receipts can be cancelled only by a Hostel Administrator, after the refund is processed in the gateway dashboard."
				),
				frappe.PermissionError,
			)
		# Cancelling reopens every allocated invoice.
		self.check_invoice_access()

	def on_cancel(self):
		from hostel360.hostel_fees.transactions import lock_invoices

		lock_invoices([row.invoice for row in self.allocations])
		self.update_invoices()
		# The day-end summary shows a cancellation on the day it happened, not on the receipt's date.
		self.db_set(
			{"cancelled_on": now_datetime(), "cancelled_by": frappe.session.user}, update_modified=False
		)

	def update_invoices(self):
		for name in sorted({row.invoice for row in self.allocations}):
			invoice = frappe.get_doc(INVOICE, name)
			invoice.update_payment_totals()

	def link_transaction(self):
		if self.payment_transaction:
			frappe.db.set_value(
				TRANSACTION, self.payment_transaction, "payment", self.name, update_modified=False
			)

	def before_print(self, settings=None):
		self.print_student = get_student_print_details(self.student, self.hostel)
		if self.received_by:
			self.received_by_name = frappe.utils.get_fullname(self.received_by)

	def get_notification_context(self) -> dict:
		return {
			"student": self.student,
			"student_name": self.student_name,
			"receipt_number": self.name,
			"amount": money(self.amount),
			"amount_formatted": format_inr(self.amount),
			"mode_of_payment": self.mode_of_payment,
			"reference_no": self.reference_no,
			"payment_date": str(self.payment_date),
			"payment_date_formatted": formatdate(self.payment_date, DISPLAY_DATE_FORMAT),
			"invoices": [row.invoice for row in self.allocations],
			"portal_link": get_portal_link("/receipts"),
		}
