"""Input normalisation and validation shared across modules."""

import re

import frappe
from frappe import _

INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
PINCODE_RE = re.compile(r"^[1-9]\d{5}$")
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def normalize_mobile(value: str | None) -> str | None:
	"""Return a 10-digit Indian mobile number, or None when empty.

	Accepts "+91 98765 43210", "09876543210", "91-9876543210" and similar forms.
	Raises ValidationError for anything that is not a valid Indian mobile number.
	"""
	if value is None:
		return None
	digits = re.sub(r"\D", "", str(value))
	if not digits:
		return None
	if len(digits) == 12 and digits.startswith("91"):
		digits = digits[2:]
	elif len(digits) == 11 and digits.startswith("0"):
		digits = digits[1:]
	if not INDIAN_MOBILE_RE.match(digits):
		frappe.throw(_("{0} is not a valid 10-digit Indian mobile number.").format(value), frappe.ValidationError)
	return digits


def is_valid_mobile(value: str | None) -> bool:
	try:
		return bool(normalize_mobile(value))
	except frappe.ValidationError:
		return False


def to_e164(mobile: str) -> str:
	"""10-digit Indian mobile to E.164 (+91XXXXXXXXXX) for SMS and WhatsApp gateways."""
	return "+91" + normalize_mobile(mobile)


def mask_mobile(mobile: str | None) -> str:
	"""98xxxxxx10 - safe to show in logs and on screens."""
	if not mobile:
		return ""
	digits = re.sub(r"\D", "", str(mobile))
	if len(digits) < 4:
		return "x" * len(digits)
	return f"{digits[:2]}{'x' * (len(digits) - 4)}{digits[-2:]}"


def mask_email(email: str | None) -> str:
	if not email or "@" not in email:
		return email or ""
	local, _sep, domain = email.partition("@")
	visible = local[:2] if len(local) > 2 else local[:1]
	return f"{visible}{'*' * max(len(local) - len(visible), 1)}@{domain}"


def validate_pincode(value: str | None) -> None:
	if value and not PINCODE_RE.match(str(value).strip()):
		frappe.throw(_("{0} is not a valid 6-digit PIN code.").format(value))


def validate_gstin(value: str | None) -> None:
	if value and not GSTIN_RE.match(str(value).strip().upper()):
		frappe.throw(_("{0} is not a valid GSTIN.").format(value))


def validate_date_range(from_date, to_date, from_label: str = "From Date", to_label: str = "To Date") -> None:
	from frappe.utils import getdate

	if from_date and to_date and getdate(from_date) > getdate(to_date):
		frappe.throw(_("{0} cannot be after {1}.").format(_(from_label), _(to_label)))
