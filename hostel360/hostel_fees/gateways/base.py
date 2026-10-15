"""Payment gateway adapter interface.

The fee module talks to a gateway only through a ``PaymentGateway`` adapter, so changing or
adding a gateway for the institution means writing an adapter, not touching invoices,
receipts or reconciliation.

Amounts cross this boundary in minor units (paise) as integers; rupee amounts never travel
as floats to a gateway.
"""

from abc import ABC, abstractmethod
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe.utils import flt

# Normalised payment statuses returned by every adapter
CAPTURED = "captured"
AUTHORIZED = "authorized"
FAILED = "failed"
REFUNDED = "refunded"
CREATED = "created"

# Normalised order statuses
ORDER_CREATED = "created"
ORDER_ATTEMPTED = "attempted"
ORDER_PAID = "paid"


class GatewayError(frappe.ValidationError):
	"""A gateway call failed. ``str(error)`` is safe to show to the payer."""

	def __init__(
		self, message: str, http_status: int | None = None, code: str | None = None, detail: str = ""
	):
		super().__init__(message)
		self.http_status = http_status
		self.code = code
		self.detail = detail


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

	def __init__(self, settings):
		self.settings = settings

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
	def verify_checkout(self, order_id: str, payment_id: str, signature: str) -> bool:
		"""Check the signature the browser checkout returned after payment."""

	@abstractmethod
	def fetch_order_status(self, order_id: str) -> dict:
		"""Returns ``{order_id, status, payments: [normalised payment, ...]}``."""

	@abstractmethod
	def verify_webhook(self, body: bytes, signature: str | None) -> bool:
		"""Check a webhook signature over the raw request body."""

	@abstractmethod
	def fetch_payment(self, payment_id: str) -> frappe._dict:
		"""Returns a normalised payment (see ``normalise_payment``)."""

	@abstractmethod
	def capture_payment(self, payment_id: str, amount: int, currency: str) -> frappe._dict:
		"""Capture an authorised payment of ``amount`` minor units."""

	@abstractmethod
	def test_connection(self) -> dict:
		"""Make a harmless authenticated call; raises GatewayError on failure."""

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
		)
