"""Payment gateway callbacks.

``razorpay_webhook`` is the only guest endpoint of the fee module. It trusts nothing in the
request until the ``X-Razorpay-Signature`` HMAC over the raw body has been verified with the
webhook secret; see ``hostel360.hostel_fees.webhooks``.
"""

import frappe
from frappe.rate_limiter import rate_limit

# Gateway deliveries arrive from a small pool of addresses; this only stops floods.
WEBHOOK_RATE_LIMIT = 600
WEBHOOK_RATE_WINDOW_SECONDS = 60


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
