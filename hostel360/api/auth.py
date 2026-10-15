"""Portal sign-in endpoints: request a code, verify it, fetch the CSRF token.

``send_otp`` and ``verify_otp`` are open to guests and return the same response shape
whether or not the number is registered. The detailed limits live in
``hostel360.auth.otp``; the decorator limits here are only a coarse flood guard, kept high
because a whole campus can share one public IP address.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from hostel360.auth import otp as otp_codes

PORTAL_REDIRECT = "/hostel"
FLOOD_LIMIT_PER_HOUR = 3000


def _client_ip() -> str | None:
	return getattr(frappe.local, "request_ip", None)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=FLOOD_LIMIT_PER_HOUR, seconds=60 * 60)
def send_otp(mobile: str, channel: str | None = None) -> dict:
	"""Send a sign-in code to a registered student or parent mobile number.

	Response: ``{sent, masked, resend_after, expires_in}`` (identical for unknown numbers).
	"""
	return otp_codes.request_code(mobile, channel=channel, ip=_client_ip())


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=FLOOD_LIMIT_PER_HOUR, seconds=60 * 60)
def verify_otp(mobile: str, otp: str, kind: str | None = None) -> dict:
	"""Verify a sign-in code and start a portal session.

	Response: ``{ok: true, kind, redirect}`` with the session cookie set, or
	``{ok: false, choose: ["student", "parent"]}`` when the number belongs to both and the
	client must repeat the call with ``kind``.
	"""
	ip = _client_ip()
	result = otp_codes.verify_code(mobile, otp, kind=kind, ip=ip)
	if not result.get("ok"):
		return {"ok": False, "choose": result.get("choose") or []}

	user = _ensure_portal_user(result["doctype"], result["name"])
	login_manager = getattr(frappe.local, "login_manager", None)
	if login_manager is None:
		frappe.throw(_("Sign-in is only possible from the portal."), frappe.AuthenticationError)
	login_manager.login_as(user)
	otp_codes.security_log("portal_login", ip=ip, kind=result["kind"])
	return {"ok": True, "kind": result["kind"], "redirect": PORTAL_REDIRECT}


@frappe.whitelist(methods=["GET", "POST"])
def get_csrf_token() -> dict:
	"""CSRF token for the current session (the SPA sends it as X-Frappe-CSRF-Token)."""
	from frappe.sessions import get_csrf_token as session_csrf_token

	return {"csrf_token": session_csrf_token()}


def _ensure_portal_user(doctype: str, name: str) -> str:
	try:
		from hostel360.hostel_people.services import ensure_portal_user
	except ImportError:
		frappe.log_error(
			title="Hostel360: portal sign-in unavailable",
			message="hostel360.hostel_people.services.ensure_portal_user could not be imported.",
		)
		frappe.throw(_("Portal sign-in is temporarily unavailable. Please try again shortly."))

	user = ensure_portal_user(doctype, name)
	if not user:
		frappe.throw(_("We could not open your portal account. Please contact the hostel office."))
	return user
