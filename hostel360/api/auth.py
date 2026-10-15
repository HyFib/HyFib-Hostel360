"""Portal sign-in endpoints: request a code, verify it, fetch the CSRF token.

``send_otp`` and ``verify_otp`` are open to guests and return the same response shape
whether or not the number is registered. The detailed limits live in
``hostel360.auth.otp``; the decorator limits here are only a coarse flood guard per IP
address. It is high because a whole campus can share one public address, and far higher
for the trusted networks listed in Messaging Settings (the campus gateway, staging load
generators), where the per-number limits still apply.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cstr

from hostel360.auth import otp as otp_codes
from hostel360.setup.roles import ADMIN_ROLE, OFFICE_ROLE, WARDEN_ROLE
from hostel360.utils.validation import mask_mobile

PORTAL_REDIRECT = "/hostel"
FLOOD_LIMIT_PER_HOUR = 3000
# Still a ceiling, so a runaway client on the campus network cannot hammer the endpoints.
TRUSTED_FLOOD_LIMIT_PER_HOUR = 100_000
# Staff who may lift a sign-in lock after confirming with the number's owner.
UNLOCK_ROLES = (ADMIN_ROLE, WARDEN_ROLE, OFFICE_ROLE, "System Manager")
# Records whose mobile number is a portal sign-in.
UNLOCK_DOCTYPES = ("Hostel Student", "Hostel Guardian")


def _client_ip() -> str | None:
	return getattr(frappe.local, "request_ip", None)


def flood_limit() -> int:
	"""Hourly request ceiling for the caller's address (see the module docstring)."""
	try:
		policy = otp_codes.get_policy()
		if otp_codes.is_trusted_ip(_client_ip(), policy.trusted_networks):
			return TRUSTED_FLOOD_LIMIT_PER_HOUR
	except Exception:
		# Settings unreadable: keep the ordinary ceiling rather than refuse sign-in.
		frappe.logger("hostel360").warning("Could not read the trusted networks for sign-in", exc_info=True)
	return FLOOD_LIMIT_PER_HOUR


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=flood_limit, seconds=60 * 60)
def send_otp(mobile: str, channel: str | None = None) -> dict:
	"""Send a sign-in code for a registered student or parent mobile number.

	``channel`` is "sms", "whatsapp" or, when Messaging Settings allows it, "email": the
	code then goes to the e-mail address on the record that holds the number.

	Response: ``{sent, masked, resend_after, expires_in}``, identical for unknown numbers
	and for numbers with no e-mail address on record; ``masked`` is always the masked mobile
	number, never an e-mail address.
	"""
	return otp_codes.request_code(mobile, channel=channel, ip=_client_ip())


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=flood_limit, seconds=60 * 60)
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


@frappe.whitelist(methods=["POST"])
def unlock_sign_in(mobile: str | None = None, doctype: str | None = None, name: str | None = None) -> dict:
	"""Lift the one-hour sign-in lock on a number (desk staff only).

	The lock follows repeated wrong codes. When the student or parent calls the office,
	staff confirm who they are and can let them try again straight away.

	``doctype`` and ``name`` (a Hostel Student or Hostel Guardian) say whose number it is,
	as the student and guardian forms send them; the saved number of that record is
	unlocked. With ``mobile`` alone, every student and guardian holding the number must
	be within the caller's hostels. Either way staff scoped to some hostels cannot lift
	the lock of another hostel's student or parent.
	"""
	frappe.only_for(UNLOCK_ROLES)
	number = _number_to_unlock(cstr(mobile).strip(), cstr(doctype).strip(), cstr(name).strip())
	otp_codes.unlock_number(number)
	otp_codes.security_log(
		"otp_number_unlocked", number, _client_ip(), by_hash=_user_hash(), record=doctype or None
	)
	return {"ok": True}


def _number_to_unlock(mobile: str, doctype: str, name: str) -> str:
	if doctype or name:
		if doctype not in UNLOCK_DOCTYPES or not name:
			frappe.throw(_("Sign-in can only be unlocked for a Hostel Student or a Hostel Guardian."))
		record = frappe.db.get_value(doctype, name, ["name", "mobile_number"], as_dict=True)
		# One answer for a missing record and for one outside the caller's hostels, so the
		# endpoint does not tell scoped staff which records exist elsewhere.
		if not record or not frappe.has_permission(doctype, "read", doc=record.name):
			_refuse_unlock(mobile)
		if not record.mobile_number:
			frappe.throw(_("{0} {1} has no mobile number saved.").format(_(doctype), frappe.bold(name)))
		number = otp_codes.normalised_number(record.mobile_number)
		if mobile and otp_codes.normalised_number(mobile) != number:
			frappe.throw(
				_("{0} is not the mobile number saved on {1}. Save the record first, then unlock.").format(
					frappe.bold(mask_mobile(mobile)), frappe.bold(name)
				)
			)
		return number

	if not mobile:
		frappe.throw(_("Please enter a valid 10-digit mobile number."))
	number = otp_codes.normalised_number(mobile)
	for holder_doctype in UNLOCK_DOCTYPES:
		holders = frappe.get_all(holder_doctype, filters={"mobile_number": number}, pluck="name")
		if not all(frappe.has_permission(holder_doctype, "read", doc=holder) for holder in holders):
			_refuse_unlock(number)
	return number


def _refuse_unlock(mobile: str | None) -> None:
	otp_codes.security_log("otp_unlock_refused", mobile, _client_ip(), by_hash=_user_hash())
	frappe.throw(
		_("You can only unlock sign-in for students and parents of the hostels you look after."),
		frappe.PermissionError,
	)


@frappe.whitelist(methods=["GET", "POST"])
def get_csrf_token() -> dict:
	"""CSRF token for the current session (the SPA sends it as X-Frappe-CSRF-Token)."""
	from frappe.sessions import get_csrf_token as session_csrf_token

	return {"csrf_token": session_csrf_token()}


def _user_hash() -> str:
	import hashlib

	return hashlib.sha256(cstr(frappe.session.user).encode()).hexdigest()[:12]


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
