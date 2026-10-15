"""Gateway registry.

``Hostel Payment Settings.gateway`` names the adapter; ``get_gateway()`` returns a ready adapter
or raises ``GatewayNotConfiguredError`` with a message that can be shown to the payer.
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

GATEWAYS = {
	"Razorpay": "hostel360.hostel_fees.gateways.razorpay.RazorpayGateway",
}

# Value used in API responses and the portal (`gateway: "razorpay"`)
GATEWAY_CODES = {
	"Razorpay": "razorpay",
}


def _settings(settings=None):
	if settings is not None:
		return settings
	from hostel360.hostel_fees.utils import get_payment_settings

	return get_payment_settings()


def get_gateway_class(gateway_name: str | None):
	path = GATEWAYS.get(gateway_name or "Razorpay")
	if not path:
		raise GatewayNotConfiguredError(_("Payment gateway {0} is not supported.").format(gateway_name))
	return frappe.get_attr(path)


def get_gateway(settings=None, require_enabled: bool = True) -> PaymentGateway:
	settings = _settings(settings)
	if require_enabled and not settings.get("enabled"):
		raise GatewayNotConfiguredError(_("Online payment is not enabled. Please pay at the hostel office."))

	gateway = get_gateway_class(settings.get("gateway"))(settings)
	if not gateway.is_configured():
		raise GatewayNotConfiguredError(
			_("Online payment is not configured yet. Please pay at the hostel office.")
		)
	return gateway


def get_webhook_gateway(settings=None) -> PaymentGateway:
	"""Adapter for verifying webhooks: needs only the webhook secret, not API keys or the enabled flag.

	Payments started before online collection was switched off must still be confirmed.
	"""
	settings = _settings(settings)
	return get_gateway_class(settings.get("gateway"))(settings)


def is_gateway_configured(settings=None) -> bool:
	"""True only when online payments are enabled and API keys are present."""
	settings = _settings(settings)
	if not settings.get("enabled"):
		return False
	try:
		return get_gateway_class(settings.get("gateway"))(settings).is_configured()
	except GatewayError:
		return False


def get_gateway_code(gateway_name: str | None) -> str:
	return GATEWAY_CODES.get(gateway_name or "Razorpay", (gateway_name or "").lower())


__all__ = [
	"GATEWAYS",
	"GatewayError",
	"GatewayNotConfiguredError",
	"PaymentGateway",
	"from_minor_units",
	"get_gateway",
	"get_gateway_code",
	"get_webhook_gateway",
	"is_gateway_configured",
	"to_minor_units",
]
