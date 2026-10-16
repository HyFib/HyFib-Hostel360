"""PayU India adapter (hosted checkout, confirmed through the Verify Payment API).

Flow: the portal posts a signed form (``checkout_options``) to PayU's payment page. PayU posts
the outcome back to ``hostel360.api.payments.payu_return`` (and to ``payu_webhook`` when the
webhook is set up in the PayU dashboard). Neither post is trusted on its own: the reverse hash
is checked with the merchant salt, and the transaction is then read back from PayU's
``verify_payment`` API before any receipt is issued.

Hashes (SHA-512, hex, fields joined with ``|``):

* request:  key|txnid|amount|productinfo|firstname|email|udf1..udf10|SALT
* response: [additionalCharges|]SALT|status|udf10..udf1|email|firstname|productinfo|amount|txnid|key
* API:      key|command|var1|SALT

Reference: https://docs.payu.in/docs/hosted-checkout-merchant-integration and
https://docs.payu.in/reference/verify_payment_api

Never log the salt or full gateway payloads: PayU posts back the payer's name, e-mail, phone
and masked card details.
"""

import hashlib
import hmac
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _

from hostel360.hostel_fees.gateways.base import (
	CAPTURED,
	CHECKOUT_REDIRECT,
	CREATED,
	FAILED,
	ORDER_ATTEMPTED,
	ORDER_CREATED,
	ORDER_PAID,
	REFUNDED,
	GatewayError,
	PaymentGateway,
	to_minor_units,
)

CHECKOUT_URLS = {
	True: "https://test.payu.in/_payment",
	False: "https://secure.payu.in/_payment",
}
API_URLS = {
	True: "https://test.payu.in/merchant/postservice.php?form=2",
	False: "https://info.payu.in/merchant/postservice.php?form=2",
}
RETURN_METHOD = "hostel360.api.payments.payu_return"
WEBHOOK_METHOD = "hostel360.api.payments.payu_webhook"

TIMEOUT_SECONDS = 10
USER_AGENT = "HyFib-Hostel360/1.0"
TXNID_MAX_LENGTH = 25
UDF_FIELDS = tuple(f"udf{i}" for i in range(1, 11))
# PayU reports its timestamps (``addedon``) in Indian Standard Time.
PAYU_TIMEZONE = ZoneInfo("Asia/Kolkata")

# PayU's detailed ("unmapped") statuses, grouped by what they mean for a receipt
REFUND_STATES = frozenset({"refunded", "partially refunded", "autorefund", "auto refund", "chargebacked"})
FAILED_STATES = frozenset({"failed", "failure", "bounced", "dropped", "usercancelled", "cancelled"})
PAYMENT_MODES = {"CC": "card", "DC": "card", "NB": "netbanking", "UPI": "upi", "CASH": "wallet", "EMI": "emi"}

# Fields of a PayU transaction that help support and audit and carry no personal data
SAFE_KEYS = (
	"mihpayid",
	"txnid",
	"status",
	"unmappedstatus",
	"mode",
	"bankcode",
	"PG_TYPE",
	"bank_ref_num",
	"amt",
	"amount",
	"transaction_amount",
	"additional_charges",
	"net_amount_debit",
	"disc",
	"discount",
	"error",
	"error_code",
	"error_Message",
	"addedon",
	"payment_source",
	"card_type",
	"issuing_bank",
	"Settled_At",
	"Merchant_UTR",
)


class PayUGateway(PaymentGateway):
	name = "PayU"
	checkout_mode = CHECKOUT_REDIRECT
	# A PayU txnid can be posted once; a retry after a failure needs a new transaction.
	reusable_orders = False

	def __init__(self, settings):
		super().__init__(settings)
		self.merchant_key = (settings.get("payu_merchant_key") or "").strip()
		self._salt = None

	# -- credentials -------------------------------------------------------------------------

	@property
	def salt(self) -> str:
		if self._salt is None:
			value = self.settings.get_password("payu_merchant_salt", raise_exception=False) or ""
			self._salt = value.strip()
		return self._salt

	@property
	def checkout_key(self) -> str:
		return self.merchant_key

	def is_configured(self) -> bool:
		return bool(self.merchant_key and self.salt)

	@property
	def checkout_url(self) -> str:
		return CHECKOUT_URLS[self.test_mode]

	@property
	def api_url(self) -> str:
		return API_URLS[self.test_mode]

	# -- hashes ------------------------------------------------------------------------------

	def request_hash(self, fields: dict) -> str:
		parts = [
			self.merchant_key,
			fields.get("txnid", ""),
			fields.get("amount", ""),
			fields.get("productinfo", ""),
			fields.get("firstname", ""),
			fields.get("email", ""),
			*(fields.get(name, "") for name in UDF_FIELDS),
			self.salt,
		]
		return sha512("|".join(str(part) for part in parts))

	def response_hash(self, fields: dict) -> str:
		parts = [
			self.salt,
			fields.get("status", ""),
			*(fields.get(name, "") for name in reversed(UDF_FIELDS)),
			fields.get("email", ""),
			fields.get("firstname", ""),
			fields.get("productinfo", ""),
			fields.get("amount", ""),
			fields.get("txnid", ""),
			fields.get("key", ""),
		]
		if fields.get("additionalCharges"):
			parts.insert(0, fields["additionalCharges"])
		return sha512("|".join(str(part or "") for part in parts))

	def verify_return(self, fields: dict) -> bool:
		fields = fields or {}
		posted = str(fields.get("hash") or "").strip().lower()
		if not (posted and self.is_configured()):
			return False
		if str(fields.get("key") or "").strip() != self.merchant_key:
			return False
		return hmac.compare_digest(self.response_hash(fields), posted)

	def return_order_id(self, fields: dict) -> str | None:
		return str((fields or {}).get("txnid") or "").strip() or None

	# -- adapter contract --------------------------------------------------------------------

	def create_order(self, amount, currency: str, receipt: str, notes: dict | None = None) -> dict:
		"""PayU has no order API: the txnid is ours, and PayU first hears of it when the form is posted.

		A random suffix keeps txnids unique for the merchant account even if a restored database
		hands out a transaction number that was already used, so a verify call can never return
		someone else's old payment.
		"""
		base = re.sub(r"[^A-Za-z0-9]", "", receipt or "") or "H360"
		txnid = f"{base}{frappe.generate_hash(length=6).upper()}"[-TXNID_MAX_LENGTH:]
		return {
			"order_id": txnid,
			"amount": to_minor_units(amount),
			"currency": (currency or "INR").upper(),
			"status": ORDER_CREATED,
			"raw": {},
		}

	def checkout_options(self, tx, context: dict) -> dict:
		"""The form the portal posts to PayU's payment page (top-level navigation, not XHR)."""
		context = frappe._dict(context or {})
		payer = frappe._dict(context.payer or {})
		return_url = get_return_url()
		fields = {
			"key": self.merchant_key,
			"txnid": tx.gateway_order_id,
			"amount": format_amount(tx.amount),
			"productinfo": clean_text(_("Hostel dues {0}").format(tx.name), 100) or "Hostel dues",
			"firstname": clean_name(payer.name) or "Student",
			"email": payer_email(payer.email),
			"phone": payer_phone(payer.contact),
			"surl": return_url,
			"furl": return_url,
			"udf1": tx.name,
		}
		fields["hash"] = self.request_hash(fields)
		return {"action_url": self.checkout_url, "method": "POST", "fields": fields}

	def fetch_order_status(self, order_id: str) -> dict:
		data = self._api("verify_payment", order_id)
		details = (data.get("transaction_details") or {}).get(order_id) or {}
		if not details or not_found(details):
			return {"order_id": order_id, "status": ORDER_CREATED, "payments": []}
		payment = self.normalise_payment(details)
		return {
			"order_id": order_id,
			"status": ORDER_PAID if payment.status == CAPTURED else ORDER_ATTEMPTED,
			"payments": [payment],
		}

	def fetch_payment(self, payment_id: str) -> frappe._dict:
		data = self._api("check_payment", payment_id)
		details = data.get("transaction_details") or {}
		if isinstance(details, dict) and str(payment_id) in details:
			details = details[str(payment_id)]
		elif isinstance(details, list):
			details = details[0] if details else {}
		if not isinstance(details, dict) or not details or not_found(details):
			raise GatewayError(
				_("The payment gateway has no payment {0}.").format(payment_id),
				http_status=404,
				code="not_found",
			)
		return self.normalise_payment(details)

	def test_connection(self) -> dict:
		# An unknown txnid is answered with "Not Found" only when the key and salt are right.
		self._api("verify_payment", "H360CONNECTIONCHECK")
		return {"ok": True, "mode": "test" if self.test_mode else "live"}

	# -- HTTP --------------------------------------------------------------------------------

	def _api(self, command: str, var1: str) -> dict:
		if not self.is_configured():
			raise GatewayError(_("Online payment is not configured. Please pay at the hostel office."))
		payload = {
			"key": self.merchant_key,
			"command": command,
			"var1": var1,
			"hash": sha512(f"{self.merchant_key}|{command}|{var1}|{self.salt}"),
		}
		try:
			response = requests.post(
				self.api_url,
				data=payload,
				timeout=TIMEOUT_SECONDS,
				headers={"Accept": "application/json", "User-Agent": USER_AGENT},
			)
		except requests.Timeout:
			self._log_failure(command, "timeout")
			raise GatewayError(
				_("The payment gateway did not respond in time. Please try again in a few minutes."),
				code="timeout",
			)
		except requests.RequestException as e:
			self._log_failure(command, f"{type(e).__name__}: {e}")
			raise GatewayError(
				_("Could not reach the payment gateway. Please try again in a few minutes."),
				code="connection_error",
			)

		if response.status_code >= 500:
			self._log_failure(command, f"HTTP {response.status_code}")
			raise GatewayError(
				_("The payment gateway is temporarily unavailable. Please try again shortly."),
				http_status=response.status_code,
			)
		try:
			data = response.json()
		except ValueError:
			data = None
		if not isinstance(data, dict):
			self._log_failure(command, f"HTTP {response.status_code}: response was not JSON")
			raise GatewayError(
				_("The payment gateway sent an unexpected response. Please try again shortly."),
				http_status=502,
				code="invalid_response",
			)

		if "transaction_details" not in data:
			message = str(data.get("msg") or data.get("message") or response.reason or "")
			self._log_failure(command, f"HTTP {response.status_code}: {message}")
			if response.status_code in (401, 403) or re.search(r"hash|key|merchant", message, re.I):
				raise GatewayError(
					_(
						"The payment gateway rejected the merchant credentials. Please contact the hostel office."
					),
					http_status=401,
					code="credentials",
					detail=message,
				)
			raise GatewayError(
				_("The payment gateway could not process the request: {0}").format(message),
				http_status=response.status_code if response.status_code >= 400 else 400,
				code="rejected",
				detail=message,
			)
		return data

	def _log_failure(self, command: str, detail: str) -> None:
		frappe.log_error(
			title=f"PayU {command} failed",
			message=f"{command}\n{detail}\nkey: {self.merchant_key[:4]}...",
		)

	# -- helpers -----------------------------------------------------------------------------

	@classmethod
	def normalise_payment(cls, details: dict | None) -> frappe._dict:
		"""A PayU transaction (verify API record or posted return fields) as a normalised payment."""
		details = details or {}
		mode = str(details.get("mode") or "").strip()
		error_description = details.get("error_Message") or details.get("field9") or None
		if error_description and str(error_description).strip().upper() == "NO ERROR":
			error_description = None
		return cls.payment(
			payment_id=str(details.get("mihpayid") or "") or None,
			order_id=str(details.get("txnid") or "") or None,
			status=payu_status(details.get("status"), details.get("unmappedstatus")),
			amount=to_minor_units(
				details.get("amt") or details.get("transaction_amount") or details.get("amount") or 0
			),
			currency="INR",
			method=PAYMENT_MODES.get(mode.upper(), mode.lower() or None),
			error_code=details.get("error_code") or details.get("error") or None,
			error_description=error_description,
			created_at=payu_timestamp(details.get("addedon")),
			raw={key: details.get(key) for key in SAFE_KEYS if key in details},
		)


def payu_status(status: str | None, unmapped: str | None) -> str:
	"""PayU's (status, unmappedstatus) as a normalised payment status.

	Only a successful, captured transaction counts as paid; an authorised or bank-pending one is
	still undecided and is looked at again later.
	"""
	status = str(status or "").strip().lower()
	unmapped = str(unmapped or "").strip().lower()
	if unmapped in REFUND_STATES:
		return REFUNDED
	if status == "success":
		return CAPTURED if unmapped in ("", "captured") else CREATED
	if status in FAILED_STATES or unmapped in FAILED_STATES:
		return FAILED
	return CREATED


def not_found(details: dict) -> bool:
	return "not found" in (
		str(details.get("status") or "").lower(),
		str(details.get("mihpayid") or "").lower(),
	)


def payu_timestamp(value) -> int | None:
	if not value:
		return None
	try:
		moment = datetime.strptime(str(value).strip()[:19], "%Y-%m-%d %H:%M:%S")
	except ValueError:
		return None
	return int(moment.replace(tzinfo=PAYU_TIMEZONE).timestamp())


def sha512(value: str) -> str:
	return hashlib.sha512(value.encode()).hexdigest()


def format_amount(amount) -> str:
	return f"{to_minor_units(amount) / 100:.2f}"


def clean_text(value, limit: int) -> str:
	"""ASCII text without the hash separator; PayU rejects most punctuation in these fields."""
	text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
	text = re.sub(r"[^A-Za-z0-9 .,_/-]", " ", text)
	return re.sub(r"\s+", " ", text).strip()[:limit]


def clean_name(value) -> str:
	text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
	return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z .]", " ", text)).strip()[:60]


def payer_email(email: str | None) -> str:
	"""PayU requires an e-mail. Students without one pay with the hostel office's address."""
	email = str(email or "").strip()
	if email and "@" in email and not email.endswith(".invalid"):
		return email
	from hostel360.hostel_fees.utils import get_hostel_setting

	office = str(get_hostel_setting("office_email") or "").strip()
	if office and "@" in office:
		return office
	host = urlparse(get_site_base_url()).hostname or "localhost"
	return f"noreply@{host}"


def payer_phone(contact: str | None) -> str:
	digits = "".join(ch for ch in str(contact or "") if ch.isdigit())
	if len(digits) >= 10:
		return digits[-10:]
	from hostel360.hostel_fees.utils import get_hostel_setting

	office = "".join(ch for ch in str(get_hostel_setting("office_phone") or "") if ch.isdigit())
	return office[-10:] if len(office) >= 10 else ""


def get_site_base_url() -> str:
	from hostel360.hostel_fees.utils import get_site_base_url as base_url

	return base_url()


def get_return_url() -> str:
	"""Where PayU posts the payer back to (both success and failure)."""
	return f"{get_site_base_url()}/api/method/{RETURN_METHOD}"


def get_webhook_url() -> str:
	return f"{get_site_base_url()}/api/method/{WEBHOOK_METHOD}"
