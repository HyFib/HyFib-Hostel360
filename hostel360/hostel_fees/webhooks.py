"""Razorpay webhook processing (endpoint: ``hostel360.api.payments.razorpay_webhook``).

Handled events: ``payment.captured`` and ``order.paid`` (issue the receipt), ``payment.failed``
(close the transaction) and ``payment.authorized`` (mark it pending; reconciliation captures it).
Webhooks are retried by the gateway, so every branch is idempotent. Unknown orders are
acknowledged and ignored: the merchant account may serve other applications too.
"""

import json

import frappe
from frappe import _

from hostel360.hostel_fees import alerting
from hostel360.hostel_fees.constants import TRANSACTION
from hostel360.hostel_fees.gateways import get_webhook_gateway
from hostel360.hostel_fees.gateways.base import CAPTURED
from hostel360.hostel_fees.gateways.razorpay import RazorpayGateway
from hostel360.hostel_fees.transactions import (
	PaymentVerificationError,
	complete_transaction,
	mark_failed,
	mark_pending,
)

SIGNATURE_ALERT_KEY = "razorpay-webhook-signature"
NOT_CONFIGURED_ALERT_KEY = "razorpay-webhook-not-configured"


def handle_razorpay_webhook(
	body: bytes, signature: str | None, event_id: str | None = None
) -> tuple[int, dict]:
	"""Verify and apply one webhook delivery. Returns (HTTP status, response body)."""
	gateway = get_webhook_gateway()

	if not gateway.webhook_secret:
		alerting.raise_alert(
			NOT_CONFIGURED_ALERT_KEY,
			_("Payment webhook received but no webhook secret is configured"),
			severity="Warning",
			category="Payments",
			description=_(
				"Set the webhook secret in Hostel Payment Settings so webhook deliveries can be verified."
			),
		)
		return 400, {"status": "not_configured"}

	if not gateway.verify_webhook(body, signature):
		alerting.raise_alert(
			SIGNATURE_ALERT_KEY,
			_("Payment webhook with an invalid signature was rejected"),
			severity="Warning",
			category="Security",
			description=_(
				"A request to the payment webhook failed signature verification and was ignored. A single "
				"occurrence usually means the webhook secret was changed in the dashboard; repeated "
				"occurrences may be an attempt to forge payment confirmations."
			),
			details={
				"ip": getattr(frappe.local, "request_ip", None),
				"event_id": event_id,
				"signature_present": bool(signature),
				"body_bytes": len(body or b""),
			},
		)
		return 400, {"status": "invalid_signature"}

	alerting.resolve_alert(NOT_CONFIGURED_ALERT_KEY, notes=_("Webhook deliveries are verified again."))

	try:
		event = json.loads(body or b"{}")
	except ValueError:
		return 400, {"status": "invalid_payload"}

	name = event.get("event") or ""
	payload = event.get("payload") or {}
	payment_entity = (payload.get("payment") or {}).get("entity") or {}
	order_entity = (payload.get("order") or {}).get("entity") or {}
	order_id = payment_entity.get("order_id") or order_entity.get("id")
	if not order_id:
		return 200, {"status": "ignored"}

	transaction = frappe.db.get_value(TRANSACTION, {"gateway_order_id": order_id}, "name")
	if not transaction:
		return 200, {"status": "ignored"}

	payment = RazorpayGateway.normalise_payment(payment_entity)
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		if name in ("payment.captured", "order.paid"):
			if payment.status != CAPTURED:
				return 200, {"status": "ignored"}
			outcome = complete_transaction(transaction, payment, "webhook")
			return 200, {"status": "ok", "transaction": transaction, "result": outcome.status}

		if name == "payment.failed":
			# A failed attempt does not close the order for good: the payer may retry and succeed,
			# and a later capture still completes the transaction.
			reason = payment.error_description or _("The payment did not go through.")
			mark_failed(transaction, reason, payment)
			return 200, {"status": "ok", "transaction": transaction}

		if name == "payment.authorized":
			mark_pending(transaction, payment)
			return 200, {"status": "ok", "transaction": transaction}

		return 200, {"status": "ignored"}
	except PaymentVerificationError:
		# Evidence and alert are already recorded; retrying the delivery would not change the answer.
		return 200, {"status": "rejected", "transaction": transaction}
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Payment webhook {name} failed for {transaction}")
		alerting.raise_alert(
			f"payment-webhook-error-{transaction}",
			_("Payment webhook could not be processed"),
			severity="Warning",
			category="Payments",
			description=_(
				"Processing {0} for transaction {1} failed. The gateway will retry, and reconciliation "
				"checks pending payments every 15 minutes."
			).format(name, transaction),
			details={"event": name, "transaction": transaction, "event_id": event_id},
		)
		return 500, {"status": "error"}
	finally:
		frappe.set_user(previous_user)
