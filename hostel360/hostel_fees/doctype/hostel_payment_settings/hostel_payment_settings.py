# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_url

from hostel360.setup.roles import ADMIN_ROLE

WEBHOOK_METHOD = "hostel360.api.payments.razorpay_webhook"
WEBHOOK_EVENTS = ("payment.captured", "payment.failed", "order.paid")
MIN_EXPIRY_MINUTES = 15
MAX_EXPIRY_MINUTES = 24 * 60


def get_webhook_url() -> str:
	from hostel360.hostel_fees.utils import get_hostel_setting

	base = (get_hostel_setting("portal_url") or get_url()).rstrip("/")
	return f"{base}/api/method/{WEBHOOK_METHOD}"


class HostelPaymentSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		enabled: DF.Check
		gateway: DF.Literal["Razorpay"]
		key_id: DF.Data | None
		key_secret: DF.Password | None
		max_reminders_per_invoice: DF.Int
		merchant_display_name: DF.Data | None
		reminder_days_before_due: DF.Int
		reminder_interval_days: DF.Int
		send_fee_reminders: DF.Check
		test_mode: DF.Check
		transaction_expiry_minutes: DF.Int
		webhook_help: DF.HTML | None
		webhook_secret: DF.Password | None
	# end: auto-generated types

	def onload(self):
		self.set_onload("webhook_url", get_webhook_url())
		self.set_onload("webhook_events", WEBHOOK_EVENTS)

	def validate(self):
		self.key_id = (self.key_id or "").strip()
		self.gateway = self.gateway or "Razorpay"

		expiry = cint(self.transaction_expiry_minutes) or 60
		if not MIN_EXPIRY_MINUTES <= expiry <= MAX_EXPIRY_MINUTES:
			frappe.throw(
				_("Transaction expiry must be between {0} and {1} minutes.").format(
					MIN_EXPIRY_MINUTES, MAX_EXPIRY_MINUTES
				)
			)
		self.transaction_expiry_minutes = expiry

		for fieldname, default in (("reminder_days_before_due", 3), ("reminder_interval_days", 3)):
			if cint(self.get(fieldname)) <= 0:
				self.set(fieldname, default)

		self.validate_keys()

	def validate_keys(self):
		if self.key_id:
			if self.key_id.startswith("rzp_live_") and self.test_mode:
				frappe.throw(
					_(
						"A live key (rzp_live_...) cannot be used in test mode. Untick Test Mode or use a test key."
					)
				)
			if self.key_id.startswith("rzp_test_") and not self.test_mode:
				frappe.throw(
					_(
						"A test key (rzp_test_...) cannot collect real payments. Tick Test Mode or use a live key."
					)
				)

		if not self.enabled:
			return
		if not self.key_id or not self.get_password("key_secret", raise_exception=False):
			frappe.throw(_("Enter the Key ID and Key Secret before enabling online payments."))
		if not self.get_password("webhook_secret", raise_exception=False):
			frappe.msgprint(
				_(
					"No webhook secret is set. Payments will still be confirmed from the checkout and by the "
					"reconciliation job every 15 minutes, but configuring the webhook confirms them instantly."
				),
				indicator="orange",
				title=_("Webhook not configured"),
			)

	def on_update(self):
		frappe.clear_document_cache(self.doctype, self.doctype)

	@frappe.whitelist(methods=["POST"])
	def test_connection(self) -> dict:
		"""Check the saved API keys with a harmless authenticated call."""
		frappe.only_for([ADMIN_ROLE, "System Manager"])
		from hostel360.hostel_fees.gateways import get_gateway

		saved = frappe.get_single(self.doctype)
		result = get_gateway(saved, require_enabled=False).test_connection()
		return {
			"ok": True,
			"mode": result.get("mode"),
			"message": _("Connected to {0} successfully ({1} mode).").format(
				saved.gateway, result.get("mode")
			),
		}
