# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, formatdate, getdate, nowdate

from hostel360.hostel_fees import notifications
from hostel360.hostel_fees.constants import (
	CANCELLED,
	DRAFT,
	FEE_HEAD,
	OVERDUE,
	PAID,
	PARTLY_PAID,
	UNPAID,
)
from hostel360.hostel_fees.utils import (
	DISPLAY_DATE_FORMAT,
	get_current_academic_year,
	get_portal_link,
	get_student_print_details,
	is_migration,
	money,
)
from hostel360.utils.jinja import format_inr


def get_allocated_total(invoice: str) -> float:
	"""Sum of submitted receipt allocations against an invoice; the source of truth for paid_amount."""
	value = frappe.db.sql(
		"""
		select coalesce(sum(alloc.allocated_amount), 0)
		from `tabHostel Payment Allocation` alloc
		inner join `tabHostel Payment` pay on pay.name = alloc.parent
		where alloc.parenttype = 'Hostel Payment'
			and alloc.invoice = %(invoice)s
			and pay.docstatus = 1
		""",
		{"invoice": invoice},
	)
	return money(value[0][0] if value else 0)


class HostelFeeInvoice(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from hostel360.hostel_fees.doctype.hostel_fee_invoice_item.hostel_fee_invoice_item import (
			HostelFeeInvoiceItem,
		)

		academic_year: DF.Link | None
		amended_from: DF.Link | None
		due_date: DF.Date
		fee_structure: DF.Link | None
		hostel: DF.Link | None
		invoice_type: DF.Literal["Hostel Fee", "Mess Bill", "Fine", "Other"]
		items: DF.Table[HostelFeeInvoiceItem]
		last_reminder_on: DF.Date | None
		migrated_paid_amount: DF.Currency
		naming_series: DF.Literal["INV-.YYYY.-.#####"]
		outstanding_amount: DF.Currency
		paid_amount: DF.Currency
		posting_date: DF.Date
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		reminder_count: DF.Int
		remarks: DF.SmallText | None
		status: DF.Literal["Draft", "Unpaid", "Partly Paid", "Paid", "Overdue", "Cancelled"]
		student: DF.Link
		student_name: DF.Data | None
		total_amount: DF.Currency
	# end: auto-generated types

	def validate(self):
		self.set_missing_values()
		self.validate_items()
		self.validate_dates()
		self.protect_migrated_amount()
		self.calculate_totals()
		self.status = self.get_status()

	def set_missing_values(self):
		if not self.posting_date:
			self.posting_date = nowdate()
		if not self.hostel:
			self.hostel = frappe.db.get_value("Hostel Student", self.student, "current_hostel")
		if self.fee_structure and not self.academic_year:
			self.academic_year = frappe.db.get_value(
				"Hostel Fee Structure", self.fee_structure, "academic_year"
			)
		if not self.academic_year:
			self.academic_year = get_current_academic_year()
		if self.fee_structure and self.is_new() and not self.reference_doctype:
			self.reference_doctype = "Hostel Fee Structure"
			self.reference_name = self.fee_structure

	def validate_items(self):
		if not self.items:
			frappe.throw(_("Add at least one fee item."))
		for row in self.items:
			row.amount = money(row.amount)
			if row.amount <= 0:
				frappe.throw(
					_("Row {0}: amount for {1} must be greater than zero.").format(row.idx, row.fee_head)
				)

		if is_migration():
			return
		heads = list({row.fee_head for row in self.items})
		inactive = frappe.get_all(FEE_HEAD, filters={"name": ("in", heads), "is_active": 0}, pluck="name")
		if inactive:
			frappe.throw(_("Fee heads {0} are inactive.").format(", ".join(inactive)))

	def validate_dates(self):
		if is_migration():
			return
		if self.due_date and self.posting_date and getdate(self.due_date) < getdate(self.posting_date):
			frappe.throw(_("Due date cannot be before the invoice date."))

	def protect_migrated_amount(self):
		"""Amounts collected before go-live may only come from the data migration.

		Read-only fields are still writable through the REST API, so the value is pinned here.
		"""
		if is_migration():
			if self.is_new() and flt(self.paid_amount) > 0 and not flt(self.migrated_paid_amount):
				self.migrated_paid_amount = money(self.paid_amount)
			else:
				self.migrated_paid_amount = money(flt(self.migrated_paid_amount))
			return
		if self.is_new():
			self.migrated_paid_amount = 0
		else:
			self.migrated_paid_amount = money(self.get_db_value("migrated_paid_amount"))

	def calculate_totals(self):
		self.total_amount = money(sum(money(row.amount) for row in self.items))
		if self.is_new() or self.docstatus == 0:
			self.paid_amount = money(self.migrated_paid_amount)
		else:
			self.paid_amount = money(flt(self.migrated_paid_amount) + get_allocated_total(self.name))
		self.outstanding_amount = money(self.total_amount - self.paid_amount)
		if self.outstanding_amount < 0:
			frappe.throw(_("The amount already paid cannot exceed the invoice total."))

	def get_status(self) -> str:
		if self.docstatus == 0:
			return DRAFT
		if self.docstatus == 2:
			return CANCELLED
		if money(self.outstanding_amount) <= 0:
			return PAID
		if self.due_date and getdate(self.due_date) < getdate(nowdate()):
			return OVERDUE
		if money(self.paid_amount) > 0:
			return PARTLY_PAID
		return UNPAID

	def on_submit(self):
		if self.invoice_type != "Mess Bill" and not self.flags.skip_notification:
			# Mess bills announce themselves through the mess module's own message.
			notifications.notify_student(
				notifications.EVENT_FEE_DUE_PUBLISHED,
				self.student,
				self.get_notification_context(),
				reference_doctype=self.doctype,
				reference_name=self.name,
			)

	def before_update_after_submit(self):
		if self.due_date and self.posting_date and getdate(self.due_date) < getdate(self.posting_date):
			frappe.throw(_("Due date cannot be before the invoice date."))

	def on_update_after_submit(self):
		# Status is not editable after submit, so it is written directly once the due date has changed.
		status = self.get_status()
		if status != self.status:
			self.db_set("status", status, notify=True)

	def before_cancel(self):
		if money(self.paid_amount) > 0:
			frappe.throw(
				_("{0} of this invoice has been paid. Cancel the receipts first.").format(
					format_inr(self.paid_amount)
				),
				title=_("Invoice has payments"),
			)
		from hostel360.hostel_fees.transactions import get_live_transactions

		live = get_live_transactions([self.name])
		if live:
			frappe.throw(
				_(
					"An online payment ({0}) for this invoice is in progress. Try again after it completes or expires."
				).format(", ".join(live)),
				title=_("Payment in progress"),
			)

	def on_cancel(self):
		self.db_set("status", CANCELLED, notify=True)

	# -- payments ----------------------------------------------------------------------------

	def update_payment_totals(self) -> None:
		"""Recompute paid, outstanding and status from submitted receipts (called with the row locked)."""
		paid = money(flt(self.migrated_paid_amount) + get_allocated_total(self.name))
		outstanding = money(self.total_amount - paid)
		if outstanding < 0:
			frappe.throw(
				_("Invoice {0} would be overpaid by {1}.").format(self.name, format_inr(-outstanding)),
				title=_("Overpayment"),
			)
		self.paid_amount = paid
		self.outstanding_amount = outstanding
		self.status = self.get_status()
		self.db_set(
			{"paid_amount": paid, "outstanding_amount": outstanding, "status": self.status},
			notify=True,
		)

	def before_print(self, settings=None):
		self.print_student = get_student_print_details(self.student)
		self.portal_link = get_portal_link("/fees")
		self.print_payments = frappe.db.sql(
			"""
			select pay.name as receipt, pay.payment_date, pay.mode_of_payment, alloc.allocated_amount
			from `tabHostel Payment Allocation` alloc
			inner join `tabHostel Payment` pay on pay.name = alloc.parent
			where alloc.parenttype = 'Hostel Payment' and alloc.invoice = %(invoice)s and pay.docstatus = 1
			order by pay.payment_date, pay.creation
			""",
			{"invoice": self.name},
			as_dict=True,
		)

	def get_notification_context(self) -> dict:
		return {
			"student": self.student,
			"student_name": self.student_name,
			"invoice": self.name,
			"invoice_type": self.invoice_type,
			"academic_year": self.academic_year,
			"amount": money(self.total_amount),
			"amount_formatted": format_inr(self.total_amount),
			"description": self.fee_structure
			or " ".join(part for part in (_(self.invoice_type), self.academic_year) if part),
			"outstanding_amount": money(self.outstanding_amount),
			"outstanding_formatted": format_inr(self.outstanding_amount),
			"due_date": str(self.due_date),
			"due_date_formatted": formatdate(self.due_date, DISPLAY_DATE_FORMAT),
			"items": [{"fee_head": row.fee_head, "amount": money(row.amount)} for row in self.items],
			"portal_link": get_portal_link("/fees"),
		}
