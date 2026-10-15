"""Razorpay adapter (Orders API + Standard Checkout).

Talks to the REST API directly with ``requests`` so the deployment carries no extra SDK.
Reference: https://razorpay.com/docs/api/orders/ and
https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/

Never log the key secret, the webhook secret or full gateway payloads: payment entities carry
the payer's contact details.
"""

import hashlib
import hmac
from urllib.parse import quote

import frappe
import requests
from frappe import _

from hostel360.hostel_fees.gateways.base import (
	AUTHORIZED,
	CAPTURED,
	ORDER_ATTEMPTED,
	ORDER_CREATED,
	ORDER_PAID,
	GatewayError,
	PaymentGateway,
	to_minor_units,
)

API_BASE = "https://api.razorpay.com/v1"
TIMEOUT_SECONDS = 10
USER_AGENT = "HyFib-Hostel360/1.0"

# Keys of a payment entity that are useful for support and audit and carry no personal data
SAFE_PAYMENT_KEYS = (
	"id",
	"entity",
	"amount",
	"currency",
	"status",
	"order_id",
	"method",
	"captured",
	"amount_refunded",
	"refund_status",
	"bank",
	"wallet",
	"international",
	"fee",
	"tax",
	"error_code",
	"error_description",
	"error_source",
	"error_step",
	"error_reason",
	"acquirer_data",
	"created_at",
)


class RazorpayGateway(PaymentGateway):
	name = "Razorpay"

	def __init__(self, settings):
		super().__init__(settings)
		self.key_id = (settings.get("key_id") or "").strip()
		self._secrets = {}

	# -- credentials -------------------------------------------------------------------------

	def _secret(self, fieldname: str) -> str:
		if fieldname not in self._secrets:
			value = self.settings.get_password(fieldname, raise_exception=False) or ""
			self._secrets[fieldname] = value.strip()
		return self._secrets[fieldname]

	@property
	def key_secret(self) -> str:
		return self._secret("key_secret")

	@property
	def webhook_secret(self) -> str:
		return self._secret("webhook_secret")

	@property
	def checkout_key(self) -> str:
		return self.key_id

	def is_configured(self) -> bool:
		return bool(self.key_id and self.key_secret)

	# -- HTTP --------------------------------------------------------------------------------

	def _request(
		self, method: str, path: str, payload: dict | None = None, params: dict | None = None
	) -> dict:
		if not self.is_configured():
			raise GatewayError(_("Online payment is not configured. Please pay at the hostel office."))

		url = f"{API_BASE}{path}"
		try:
			response = requests.request(
				method,
				url,
				auth=(self.key_id, self.key_secret),
				json=payload,
				params=params,
				timeout=TIMEOUT_SECONDS,
				headers={"Accept": "application/json", "User-Agent": USER_AGENT},
			)
		except requests.Timeout:
			self._log_failure(method, path, "timeout")
			raise GatewayError(
				_("The payment gateway did not respond in time. Please try again in a few minutes."),
				code="timeout",
			)
		except requests.RequestException as e:
			self._log_failure(method, path, f"{type(e).__name__}: {e}")
			raise GatewayError(
				_("Could not reach the payment gateway. Please try again in a few minutes."),
				code="connection_error",
			)

		try:
			data = response.json()
		except ValueError:
			data = {}

		if response.status_code >= 400:
			error = (data or {}).get("error") or {}
			code = error.get("code") or ""
			description = error.get("description") or response.reason or ""
			self._log_failure(method, path, f"HTTP {response.status_code} {code}: {description}")
			if response.status_code == 401:
				message = _(
					"The payment gateway rejected the merchant credentials. Please contact the hostel office."
				)
			elif response.status_code >= 500:
				message = _("The payment gateway is temporarily unavailable. Please try again shortly.")
			else:
				message = _("The payment gateway could not process the request: {0}").format(description)
			raise GatewayError(message, http_status=response.status_code, code=code, detail=description)

		return data if isinstance(data, dict) else {}

	def _log_failure(self, method: str, path: str, detail: str) -> None:
		frappe.log_error(
			title=f"Razorpay {method} {path.split('?')[0]} failed",
			message=f"{method} {path}\n{detail}\nkey: {self.key_id[:12]}...",
		)

	# -- adapter contract --------------------------------------------------------------------

	def create_order(self, amount, currency: str, receipt: str, notes: dict | None = None) -> dict:
		payload = {
			"amount": to_minor_units(amount),
			"currency": (currency or "INR").upper(),
			"receipt": (receipt or "")[:40],
			"notes": clean_notes(notes),
		}
		data = self._request("POST", "/orders", payload)
		if not data.get("id"):
			raise GatewayError(_("The payment gateway did not return an order. Please try again."))
		return {
			"order_id": data["id"],
			"amount": int(data.get("amount") or payload["amount"]),
			"currency": data.get("currency") or payload["currency"],
			"status": data.get("status"),
			"raw": data,
		}

	def verify_checkout(self, order_id: str, payment_id: str, signature: str) -> bool:
		if not (order_id and payment_id and signature and self.key_secret):
			return False
		expected = hmac.new(
			self.key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
		).hexdigest()
		return hmac.compare_digest(expected, str(signature).strip())

	def verify_webhook(self, body: bytes, signature: str | None) -> bool:
		secret = self.webhook_secret
		if not (secret and signature):
			return False
		if isinstance(body, str):
			body = body.encode()
		expected = hmac.new(secret.encode(), body or b"", hashlib.sha256).hexdigest()
		return hmac.compare_digest(expected, str(signature).strip())

	def fetch_order_status(self, order_id: str) -> dict:
		data = self._request("GET", f"/orders/{quote(order_id, safe='')}/payments")
		payments = [self.normalise_payment(item) for item in (data.get("items") or [])]
		if any(p.status == CAPTURED for p in payments):
			status = ORDER_PAID
		elif payments:
			status = ORDER_ATTEMPTED
		else:
			status = ORDER_CREATED
		return {"order_id": order_id, "status": status, "payments": payments}

	def fetch_payment(self, payment_id: str) -> frappe._dict:
		data = self._request("GET", f"/payments/{quote(payment_id, safe='')}")
		return self.normalise_payment(data)

	def capture_payment(self, payment_id: str, amount: int, currency: str) -> frappe._dict:
		data = self._request(
			"POST",
			f"/payments/{quote(payment_id, safe='')}/capture",
			{"amount": int(amount), "currency": (currency or "INR").upper()},
		)
		return self.normalise_payment(data)

	def test_connection(self) -> dict:
		self._request("GET", "/orders", params={"count": 1})
		return {"ok": True, "mode": "test" if self.key_id.startswith("rzp_test_") else "live"}

	# -- helpers -----------------------------------------------------------------------------

	@classmethod
	def normalise_payment(cls, entity: dict | None) -> frappe._dict:
		entity = entity or {}
		return cls.payment(
			payment_id=entity.get("id"),
			order_id=entity.get("order_id"),
			status=entity.get("status"),
			amount=entity.get("amount") or 0,
			currency=entity.get("currency"),
			method=entity.get("method"),
			error_code=entity.get("error_code"),
			error_description=entity.get("error_description"),
			created_at=entity.get("created_at"),
			raw={key: entity.get(key) for key in SAFE_PAYMENT_KEYS if key in entity},
		)


def clean_notes(notes: dict | None) -> dict:
	"""Razorpay accepts at most 15 notes of up to 256 characters each."""
	cleaned = {}
	for key, value in (notes or {}).items():
		if value in (None, ""):
			continue
		cleaned[str(key)[:40]] = str(value)[:256]
		if len(cleaned) == 15:
			break
	return cleaned


def is_successful(payment: dict) -> bool:
	return (payment or {}).get("status") in (CAPTURED, AUTHORIZED)
