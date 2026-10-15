# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from hostel360.hostel_fees.constants import (
	CLOSED_TX_STATUSES,
	LIVE_TX_STATUSES,
	PAYMENT,
	TX_EXPIRED,
	TX_REFUNDED,
	TX_SUCCESS,
)
from hostel360.setup.roles import ADMIN_ROLE, OFFICE_ROLE


class HostelPaymentTransaction(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from hostel360.hostel_fees.doctype.hostel_payment_transaction_invoice.hostel_payment_transaction_invoice import (
			HostelPaymentTransactionInvoice,
		)

		amount: DF.Currency
		completed_on: DF.Datetime | None
		currency: DF.Data | None
		failure_reason: DF.SmallText | None
		gateway: DF.Data | None
		gateway_order_id: DF.Data | None
		gateway_payment_id: DF.Data | None
		gateway_response: DF.Code | None
		hostel: DF.Link | None
		initiated_by: DF.Link | None
		invoices: DF.Table[HostelPaymentTransactionInvoice]
		ip_address: DF.Data | None
		last_checked_on: DF.Datetime | None
		naming_series: DF.Literal["PTX-.YYYY.-.######"]
		payment: DF.Link | None
		payment_method: DF.Data | None
		reconciled_on: DF.Datetime | None
		refunded_amount: DF.Currency
		remarks: DF.SmallText | None
		status: DF.Literal["Initiated", "Pending", "Success", "Failed", "Expired", "Refunded"]
		student: DF.Link
		student_name: DF.Data | None
		test_mode: DF.Check
		verification_failed: DF.Check
	# end: auto-generated types

	def validate(self):
		if not self.currency:
			self.currency = "INR"
		if not self.hostel:
			from hostel360.hostel_fees.utils import get_student_hostel

			self.hostel = get_student_hostel(self.student)

	def on_trash(self):
		frappe.throw(_("Payment transactions are part of the audit trail and cannot be deleted."))

	@frappe.whitelist(methods=["POST"])
	def check_with_gateway(self) -> dict:
		"""Staff action: ask the gateway what happened to this payment now, instead of waiting for
		the next reconciliation run. Works on closed transactions too, because their order may
		still have been paid; it needs only the gateway's keys, not online payments enabled."""
		frappe.only_for([ADMIN_ROLE, OFFICE_ROLE, "System Manager"])
		if self.status not in LIVE_TX_STATUSES + CLOSED_TX_STATUSES or (
			self.status in CLOSED_TX_STATUSES and self.payment
		):
			return {
				"status": self.status,
				"message": _("This transaction is already {0}.").format(_(self.status)),
			}

		from hostel360.hostel_fees.gateways import get_settlement_gateway
		from hostel360.hostel_fees.transactions import sync_with_gateway

		status = sync_with_gateway(self.name, get_settlement_gateway(self.gateway), "desk")
		result = {"status": status, "payment": frappe.db.get_value(self.doctype, self.name, "payment")}
		if status == self.status and status in CLOSED_TX_STATUSES:
			result["message"] = _("The gateway shows no new completed payment for this transaction.")
		return result

	@frappe.whitelist(methods=["POST"])
	def close_as_expired(self, reason: str) -> dict:
		"""Close an open transaction the gateway can no longer read (for example a test-mode order
		after switching to live keys), after checking the gateway dashboard."""
		frappe.only_for([ADMIN_ROLE, "System Manager"])
		reason = (reason or "").strip()
		if not reason:
			frappe.throw(_("Enter why the transaction is being closed."))
		if self.status not in LIVE_TX_STATUSES:
			frappe.throw(_("Only open (Initiated or Pending) transactions can be closed."))

		from hostel360.hostel_fees.transactions import mark_failed

		status = mark_failed(
			self.name,
			_("Closed by {0}: {1}").format(frappe.session.user, reason),
			status=TX_EXPIRED,
		)
		self.add_comment("Info", _("Closed as expired: {0}").format(frappe.utils.escape_html(reason)))
		return {"status": status}

	@frappe.whitelist(methods=["POST"])
	def mark_refunded(self, remarks: str) -> dict:
		"""Record that the money was returned through the gateway dashboard."""
		frappe.only_for([ADMIN_ROLE, "System Manager"])
		remarks = (remarks or "").strip()
		if not remarks:
			frappe.throw(_("Enter the refund reference and reason."))
		if self.status != TX_SUCCESS:
			frappe.throw(_("Only successful payments can be marked as refunded."))
		if self.payment and frappe.db.get_value(PAYMENT, self.payment, "docstatus") == 1:
			frappe.throw(
				_("Cancel receipt {0} before marking the payment as refunded.").format(self.payment),
				title=_("Receipt still active"),
			)
		self.db_set(
			{
				"status": TX_REFUNDED,
				"remarks": _("Refunded by {0}: {1}").format(frappe.session.user, remarks)[:1000],
			},
			notify=True,
		)
		self.add_comment("Info", _("Marked as refunded: {0}").format(frappe.utils.escape_html(remarks)))
		return {"status": TX_REFUNDED}
