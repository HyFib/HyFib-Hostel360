"""Public verification of bonafide certificates.

Banks, embassies and scholarship offices scan the QR code on a certificate (or type its
code) to confirm it was issued by the hostel. Only what is needed to match the paper is
disclosed: the certificate number, a masked name, the programme and the validity dates.
"""

import hmac
import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import formatdate, getdate, nowdate

from hostel360.student_services.utils import mask_name

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16}$")
CERTIFIED_STATUSES = ("Approved", "Issued")

# Lookups allowed per IP address in the window; generous for an office verifying a batch
# of certificates, far too few to enumerate codes.
LOOKUP_LIMIT = 30
LOOKUP_WINDOW_SECONDS = 10 * 60


def normalise_code(code: str | None) -> str:
	return (code or "").strip()


def lookup_certificate(code: str | None) -> dict:
	"""Result of a verification: ``state`` is "valid", "expired" or "not_found"."""
	code = normalise_code(code)
	if not CODE_RE.match(code):
		return {"state": "not_found"}

	row = frappe.db.get_value(
		"Bonafide Certificate Request",
		{"verification_code": code, "status": ["in", CERTIFIED_STATUSES]},
		[
			"verification_code",
			"certificate_number",
			"student_name",
			"program",
			"issue_date",
			"valid_till",
			"purpose",
		],
		as_dict=True,
	)
	# The database compares case-insensitively; codes are case-sensitive.
	if not row or not hmac.compare_digest(row.verification_code.encode(), code.encode()):
		return {"state": "not_found"}

	expired = bool(row.valid_till) and getdate(row.valid_till) < getdate(nowdate())
	return {
		"state": "expired" if expired else "valid",
		"certificate_number": row.certificate_number,
		"student_name": mask_name(row.student_name),
		"program": row.program or "",
		"purpose": row.purpose or "",
		"issue_date": formatdate(row.issue_date) if row.issue_date else "",
		"valid_till": formatdate(row.valid_till) if row.valid_till else "",
	}


def is_rate_limited(ip: str | None = None) -> bool:
	"""Counts a lookup for the caller's IP and says whether it is over the limit."""
	ip = ip or getattr(frappe.local, "request_ip", None) or "unknown"
	key = frappe.cache.make_key(f"rl:hostel360:verify-certificate:{ip}")
	count = frappe.cache.incrby(key, 1)
	if count == 1:
		frappe.cache.expire(key, LOOKUP_WINDOW_SECONDS)
	return count > LOOKUP_LIMIT


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
@rate_limit(limit=LOOKUP_LIMIT, seconds=LOOKUP_WINDOW_SECONDS)
def verify(code: str) -> dict:
	"""JSON verification for agencies that check certificates in bulk."""
	result = lookup_certificate(code)
	result["message"] = {
		"valid": _("This certificate is genuine and valid."),
		"expired": _("This certificate is genuine but its validity has ended."),
		"not_found": _("No certificate matches this code."),
	}[result["state"]]
	return result
