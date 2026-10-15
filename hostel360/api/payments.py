"""Payment gateway callbacks (guest endpoints).

``razorpay_webhook`` trusts nothing in the request until the ``X-Razorpay-Signature`` HMAC over
the raw body has been verified with the webhook secret.

``payu_return`` is where PayU sends the payer's browser back (``surl`` and ``furl``), and
``payu_webhook`` receives PayU's server-to-server notifications. Both check PayU's response hash
with the merchant salt and then ask PayU's verify API for the real outcome before anything is
receipted; see ``hostel360.hostel_fees.webhooks`` and ``services.confirm_gateway_return``.

The return post arrives as a cross-site top-level navigation, so (with ``SameSite=Lax`` session
cookies) it carries no session: it is handled as a guest and ends with a redirect to the portal's
fees page, where the signed-in payer sees the outcome.
"""

from urllib.parse import urlencode

import frappe
from frappe.rate_limiter import rate_limit
from werkzeug.utils import redirect

# Gateway deliveries arrive from a small pool of addresses; this only stops floods.
WEBHOOK_RATE_LIMIT = 600
WEBHOOK_RATE_WINDOW_SECONDS = 60
# Return posts come from payers' own browsers, one per payment.
RETURN_RATE_LIMIT = 30
RETURN_RATE_WINDOW_SECONDS = 60

PORTAL_FEES_PATH = "/hostel/fees"
# Bounds what a guest post may make us parse; PayU sends a few dozen short fields.
MAX_POSTED_FIELDS = 120
MAX_FIELD_LENGTH = 2000


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=WEBHOOK_RATE_LIMIT, seconds=WEBHOOK_RATE_WINDOW_SECONDS)
def razorpay_webhook():
	from hostel360.hostel_fees.webhooks import handle_razorpay_webhook

	body = frappe.request.get_data(cache=True) or b""
	status_code, response = handle_razorpay_webhook(
		body,
		frappe.get_request_header("X-Razorpay-Signature"),
		frappe.get_request_header("X-Razorpay-Event-Id"),
	)
	frappe.local.response["http_status_code"] = status_code
	return response


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=RETURN_RATE_LIMIT, seconds=RETURN_RATE_WINDOW_SECONDS)
def payu_return():
	"""PayU posts the payer back here after paying (or failing); redirect them to their fees page."""
	from hostel360.hostel_fees.services import confirm_gateway_return

	try:
		outcome = confirm_gateway_return("PayU", _posted_fields(), source="checkout")
	except Exception:
		# The payer is waiting in their browser and an error page would leave them guessing.
		# Whatever failed (a deadlock that rolled the receipt back, for example), the webhook
		# and the 15-minute reconciliation settle the payment with PayU.
		frappe.db.rollback()
		frappe.log_error(title="PayU return could not be processed")
		return fees_redirect(None, "pending")
	return fees_redirect(outcome.transaction, portal_status(outcome))


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=WEBHOOK_RATE_LIMIT, seconds=WEBHOOK_RATE_WINDOW_SECONDS)
def payu_webhook():
	from hostel360.hostel_fees.webhooks import handle_payu_webhook

	status_code, response = handle_payu_webhook(_posted_fields())
	frappe.local.response["http_status_code"] = status_code
	return response


def portal_status(outcome) -> str:
	"""``success``, ``failed`` or ``pending`` for the portal's fees page."""
	if outcome.result == "unknown":
		return "failed"
	if outcome.status == "Success":
		return "success"
	if outcome.status in ("Failed", "Expired") and outcome.result in ("ok", "rejected"):
		return "failed"
	return "pending"


def fees_redirect(transaction: str | None, status: str):
	"""303 so the browser follows with a GET and a reload never re-posts the payment result."""
	query = {"payment": transaction, "status": status} if transaction else {"status": status}
	return redirect(f"{PORTAL_FEES_PATH}?{urlencode(query)}", code=303)


def _posted_fields() -> dict:
	fields = {}
	for key, value in (frappe.form_dict or {}).items():
		if key == "cmd" or len(fields) >= MAX_POSTED_FIELDS:
			continue
		if isinstance(value, list | tuple):
			value = value[0] if value else ""
		fields[str(key)[:64]] = "" if value is None else str(value)[:MAX_FIELD_LENGTH]
	return fields
