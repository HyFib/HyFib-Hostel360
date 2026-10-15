"""Payment gateway adapter interface.

The fee module talks to a gateway only through a ``PaymentGateway`` adapter, so changing or
adding a gateway for the institution means writing an adapter, not touching invoices,
receipts or reconciliation.

Two checkout styles are supported:

* ``modal`` (Razorpay): the portal opens the gateway's script checkout and sends the signed
  result to ``hostel360.api.portal.verify_payment``.
* ``redirect`` (PayU): the portal posts a signed form to the gateway's page and the gateway
  posts the payer back to a Hostel360 return URL.

Either way the browser is never trusted: the payment is read back from the gateway before a
receipt is issued.

Amounts cross this boundary in minor units (paise) as integers; rupee amounts never travel
as floats to a gateway.
"""

from abc import ABC, abstractmethod
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt

# Normalised payment statuses returned by every adapter. CREATED also covers an attempt the
# gateway has not decided yet (pending with the bank).
CAPTURED = "captured"
AUTHORIZED = "authorized"
FAILED = "failed"
REFUNDED = "refunded"
CREATED = "created"

# Normalised order statuses
ORDER_CREATED = "created"
ORDER_ATTEMPTED = "attempted"
ORDER_PAID = "paid"

CHECKOUT_MODAL = "modal"
CHECKOUT_REDIRECT = "redirect"


class GatewayError(frappe.ValidationError):
	"""A gateway call failed. ``str(error)`` is safe to show to the payer."""

	def __init__(
		self, message: str, http_status: int | None = None, code: str | None = None, detail: str = ""
	):
		super().__init__(message)
		self.http_status = http_status
		self.code = code
		self.detail = detail

	@property
	def is_unreachable(self) -> bool:
		"""The gateway could not be asked at all (network, outage, credentials, throttling).

		Any other error means the gateway answered but could not read this particular order.
		"""
		return (
			self.code in ("timeout", "connection_error", "invalid_response")
			or self.http_status in (401, 403, 429)
			or cint(self.http_status) >= 500
		)


class GatewayNotConfiguredError(GatewayError):
	pass


def to_minor_units(amount) -> int:
	"""Rupees to paise without float drift: 199.99 -> 19999."""
	return int((Decimal(str(flt(amount, 2))) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_minor_units(value) -> float:
	return flt(Decimal(int(value or 0)) / Decimal(100), 2)


class PaymentGateway(ABC):
	"""Adapter contract. Implementations must be stateless apart from the settings they wrap."""

	name: str = ""
	checkout_mode: str = CHECKOUT_MODAL
	# True when the same order can be paid again after a failed attempt (the payer retries inside
	# the gateway's checkout), so a failed attempt does not close the transaction and a repeat
	# request for the same dues reuses the order. False when every attempt needs a fresh order.
	reusable_orders: bool = True

	def __init__(self, settings):
		self.settings = settings

	@property
	def test_mode(self) -> bool:
		"""The credentials in use belong to the gateway's test environment."""
		return bool(cint(self.settings.get("test_mode")))

	@property
	def checkout_key(self) -> str:
		"""Public key handed to the browser checkout."""
		return ""

	@abstractmethod
	def is_configured(self) -> bool:
		"""Credentials needed for API calls are present."""

	@abstractmethod
	def create_order(self, amount, currency: str, receipt: str, notes: dict | None = None) -> dict:
		"""Create an order for ``amount`` rupees.

		Returns ``{order_id, amount (minor units), currency, status, raw}``.
		"""

	@abstractmethod
	def checkout_options(self, tx, context: dict) -> dict:
		"""Data the portal needs to open this gateway's checkout for transaction ``tx``.

		``context`` carries ``title``, ``description``, ``payer`` (``name``, ``email``,
		``contact``), ``image`` and ``timeout`` (seconds left before the transaction expires).
		"""

	@abstractmethod
	def fetch_order_status(self, order_id: str) -> dict:
		"""Returns ``{order_id, status, payments: [normalised payment, ...]}``."""

	@abstractmethod
	def fetch_payment(self, payment_id: str) -> frappe._dict:
		"""Returns a normalised payment (see ``payment``)."""

	@abstractmethod
	def test_connection(self) -> dict:
		"""Make a harmless authenticated call; raises GatewayError on failure."""

	def verify_checkout(self, order_id: str, payment_id: str, signature: str) -> bool:
		"""Check the signature a modal checkout returned after payment."""
		return False

	def verify_return(self, fields: dict) -> bool:
		"""Check the signed fields a redirect checkout posted back."""
		return False

	def return_order_id(self, fields: dict) -> str | None:
		"""The order a redirect checkout's posted fields refer to."""
		return None

	def verify_webhook(self, body: bytes, signature: str | None) -> bool:
		"""Check a webhook signature over the raw request body."""
		return False

	def capture_payment(self, payment_id: str, amount: int, currency: str) -> frappe._dict:
		"""Capture an authorised payment of ``amount`` minor units."""
		raise GatewayError(_("{0} captures payments automatically.").format(self.name), code="unsupported")

	@staticmethod
	def payment(
		payment_id: str | None,
		order_id: str | None,
		status: str | None,
		amount: int,
		currency: str | None,
		method: str | None = None,
		error_code: str | None = None,
		error_description: str | None = None,
		created_at: int | None = None,
		raw: dict | None = None,
		amount_refunded: int = 0,
	) -> frappe._dict:
		"""Normalised payment record shared by all adapters."""
		return frappe._dict(
			payment_id=payment_id,
			order_id=order_id,
			status=(status or "").lower(),
			amount=int(amount or 0),
			currency=(currency or "").upper(),
			method=method,
			error_code=error_code,
			error_description=error_description,
			created_at=created_at,
			raw=raw or {},
			amount_refunded=int(amount_refunded or 0),
		)
