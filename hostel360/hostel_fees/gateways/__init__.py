"""Gateway registry.

``Hostel Payment Settings.gateway`` names the adapter used for new payments. A transaction keeps
the gateway it was started with, so payments in flight when the institution switches gateways
are still confirmed by the gateway that took them.

``get_gateway()`` returns a ready adapter or raises ``GatewayNotConfiguredError`` with a message
that can be shown to the payer. Starting a payment needs online payments enabled; confirming
or settling one that already exists needs only the gateway's credentials.
"""

import frappe
from frappe import _

from hostel360.hostel_fees.gateways.base import (
	GatewayError,
	GatewayNotConfiguredError,
	PaymentGateway,
	from_minor_units,
	to_minor_units,
)

DEFAULT_GATEWAY = "Razorpay"

GATEWAYS = {
	"Razorpay": "hostel360.hostel_fees.gateways.razorpay.RazorpayGateway",
	"PayU": "hostel360.hostel_fees.gateways.payu.PayUGateway",
}

# Value used in API responses and the portal (`gateway: "razorpay"`)
GATEWAY_CODES = {
	"Razorpay": "razorpay",
	"PayU": "payu",
}


def _settings(settings=None):
	if settings is not None:
		return settings
	from hostel360.hostel_fees.utils import get_payment_settings

	return get_payment_settings()


def get_gateway_class(gateway_name: str | None):
	path = GATEWAYS.get(gateway_name or DEFAULT_GATEWAY)
	if not path:
		raise GatewayNotConfiguredError(_("Payment gateway {0} is not supported.").format(gateway_name))
	return frappe.get_attr(path)


def get_gateway(
	settings=None, require_enabled: bool = True, gateway_name: str | None = None
) -> PaymentGateway:
	"""Adapter for ``gateway_name`` (default: the gateway selected in the settings).

	``require_enabled`` is for starting payments only; pass False to confirm or settle a payment
	that was started before online collection was switched off.
	"""
	settings = _settings(settings)
	if require_enabled and not settings.get("enabled"):
		raise GatewayNotConfiguredError(_("Online payment is not enabled. Please pay at the hostel office."))

	gateway = get_gateway_class(gateway_name or settings.get("gateway"))(settings)
	if not gateway.is_configured():
		raise GatewayNotConfiguredError(
			_("Online payment is not configured yet. Please pay at the hostel office.")
		)
	return gateway


def get_settlement_gateway(gateway_name: str | None, settings=None) -> PaymentGateway:
	"""Adapter that confirms an existing transaction: its own gateway, credentials only."""
	return get_gateway(settings, require_enabled=False, gateway_name=gateway_name)


def get_webhook_gateway(settings=None, gateway_name: str | None = None) -> PaymentGateway:
	"""Adapter for verifying webhooks: needs only the webhook secret, not API keys or the enabled flag.

	Payments started before online collection was switched off must still be confirmed.
	"""
	settings = _settings(settings)
	return get_gateway_class(gateway_name or settings.get("gateway"))(settings)


def is_gateway_configured(settings=None) -> bool:
	"""True only when online payments are enabled and the selected gateway's keys are present."""
	settings = _settings(settings)
	if not settings.get("enabled"):
		return False
	try:
		return get_gateway_class(settings.get("gateway"))(settings).is_configured()
	except GatewayError:
		return False


class SettlementGateways:
	"""Adapters for settling existing transactions, one per gateway, created on first use.

	A gateway whose credentials are missing maps to None so callers can skip its transactions.
	"""

	def __init__(self, settings=None):
		self.settings = _settings(settings)
		self._adapters = {}

	def get(self, gateway_name: str | None) -> PaymentGateway | None:
		gateway_name = gateway_name or DEFAULT_GATEWAY
		if gateway_name not in self._adapters:
			try:
				self._adapters[gateway_name] = get_settlement_gateway(gateway_name, self.settings)
			except GatewayNotConfiguredError:
				self._adapters[gateway_name] = None
		return self._adapters[gateway_name]

	def any_configured(self) -> bool:
		return any(self.get(name) for name in GATEWAYS)


def get_gateway_code(gateway_name: str | None) -> str:
	return GATEWAY_CODES.get(gateway_name or DEFAULT_GATEWAY, (gateway_name or "").lower())


__all__ = [
	"GATEWAYS",
	"GatewayError",
	"GatewayNotConfiguredError",
	"PaymentGateway",
	"SettlementGateways",
	"from_minor_units",
	"get_gateway",
	"get_gateway_code",
	"get_settlement_gateway",
	"get_webhook_gateway",
	"is_gateway_configured",
	"to_minor_units",
]
