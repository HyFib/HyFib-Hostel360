"""Gateway webhook processing.

Razorpay (endpoint ``hostel360.api.payments.razorpay_webhook``): ``payment.captured`` and
``order.paid`` issue the receipt, ``payment.failed`` records the failed attempt (the payer may
still retry on the same order, so the transaction stays open until it expires),
``payment.authorized`` marks it pending (reconciliation captures it) and ``refund.created`` /
``refund.processed`` record money returned through the dashboard.

PayU (endpoint ``hostel360.api.payments.payu_webhook``): the posted fields are hash-verified and
the transaction is then read back from PayU's verify API, exactly like the payer's return post.

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
from hostel360.hostel_fees.transactions import (
	PaymentVerificationError,
	complete_transaction,
	mark_pending,
	record_gateway_refund,
)

SIGNATURE_ALERT_KEY = "razorpay-webhook-signature"
NOT_CONFIGURED_ALERT_KEY = "razorpay-webhook-not-configured"
PAYU_NOT_CONFIGURED_ALERT_KEY = "payu-webhook-not-configured"
COMPLETION_EVENTS = ("payment.captured", "order.paid")
REFUND_EVENTS = ("refund.created", "refund.processed")


def handle_razorpay_webhook(
	body: bytes, signature: str | None, event_id: str | None = None
) -> tuple[int, dict]:
	"""Verify and apply one webhook delivery. Returns (HTTP status, response body)."""
	# Razorpay deliveries are verified with Razorpay's secret even after the institution has
	# switched to another gateway: payments started before the switch still report here.
	gateway = get_webhook_gateway(gateway_name="Razorpay")

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

	try:
		event = json.loads(body or b"{}")
	except ValueError:
		return 400, {"status": "invalid_payload"}

	name = event.get("event") or ""
	payload = event.get("payload") or {}
	payment_entity = (payload.get("payment") or {}).get("entity") or {}
	order_entity = (payload.get("order") or {}).get("entity") or {}
	order_id = payment_entity.get("order_id") or order_entity.get("id")
	transaction = (
		frappe.db.get_value(TRANSACTION, {"gateway_order_id": order_id}, "name") if order_id else None
	)
	if not transaction:
		_delivered(None)
		return 200, {"status": "ignored"}

	payment = gateway.normalise_payment(payment_entity)
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		if name in COMPLETION_EVENTS:
			if payment.status != CAPTURED:
				response = {"status": "ignored"}
			else:
				outcome = complete_transaction(transaction, payment, "webhook")
				response = {"status": "ok", "transaction": transaction, "result": outcome.status}
		elif name == "payment.failed":
			# A failed attempt does not close the order: the payer may retry and succeed in the same
			# checkout. The transaction stays open (and its dues guarded) until it expires.
			reason = payment.error_description or _("The payment did not go through.")
			mark_pending(transaction, payment, reason=reason)
			response = {"status": "ok", "transaction": transaction}
		elif name == "payment.authorized":
			mark_pending(transaction, payment)
			response = {"status": "ok", "transaction": transaction}
		elif name in REFUND_EVENTS:
			record_gateway_refund(transaction, payment, "webhook")
			response = {"status": "ok", "transaction": transaction}
		else:
			response = {"status": "ignored"}
		_delivered(transaction)
		return 200, response
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


def _delivered(transaction: str | None, not_configured_key: str = NOT_CONFIGURED_ALERT_KEY) -> None:
	"""A verified delivery was processed: close alerts an earlier failed delivery left open.

	Done after processing, because a retried row lock rolls back anything written before it.
	"""
	alerting.resolve_alert(not_configured_key, notes=_("Webhook deliveries are verified again."))
	if transaction:
		alerting.resolve_alert(
			f"payment-webhook-error-{transaction}",
			notes=_("A later delivery for this transaction was processed."),
		)


def handle_payu_webhook(fields: dict) -> tuple[int, dict]:
	"""Verify and apply one PayU webhook post. Returns (HTTP status, response body)."""
	from hostel360.hostel_fees.services import confirm_gateway_return

	outcome = confirm_gateway_return("PayU", fields, source="webhook")
	if outcome.result == "not_configured":
		alerting.raise_alert(
			PAYU_NOT_CONFIGURED_ALERT_KEY,
			_("PayU webhook received but the merchant key and salt are not configured"),
			severity="Warning",
			category="Payments",
			description=_("Enter the PayU merchant key and salt in Hostel Payment Settings."),
		)
		return 400, {"status": "not_configured"}
	if outcome.result == "invalid_signature":
		return 400, {"status": "invalid_signature"}
	if outcome.result == "unknown":
		_delivered(None, PAYU_NOT_CONFIGURED_ALERT_KEY)
		return 200, {"status": "ignored"}
	if outcome.result == "unverified":
		# PayU retries deliveries that fail; reconciliation is the backstop either way.
		return 503, {"status": "retry", "transaction": outcome.transaction}
	_delivered(outcome.transaction, PAYU_NOT_CONFIGURED_ALERT_KEY)
	return 200, {
		"status": "rejected" if outcome.result == "rejected" else "ok",
		"transaction": outcome.transaction,
		"result": outcome.status,
	}
