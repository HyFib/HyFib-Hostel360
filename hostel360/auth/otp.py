"""One-time sign-in codes for the student and parent portal.

Codes are random digits from ``secrets``. Only an HMAC-SHA256 of the code (keyed with a
key derived from the site's encryption key and bound to the mobile number) is kept, in
Redis, with the code's lifetime as TTL. Redis keys carry an HMAC of the mobile number,
never the number itself.

Abuse controls, all per site:

* per number: a resend cooldown and ``otp_max_sends_per_hour`` codes an hour;
* per IP address: ``otp_max_sends_per_ip_per_hour`` codes an hour (trusted networks such
  as the campus Wi-Fi gateway are exempt) and a ceiling on failed verifications;
* per code: ``otp_max_attempts`` wrong guesses invalidate it;
* per number: ``FAILED_VERIFICATIONS_PER_HOUR`` failures lock the number for an hour.

Requests for numbers that are not registered get exactly the same response, take about
as long as a real send, and count against the same limits, so the endpoint cannot be
used to find out who lives in the hostel.

Development shortcut: with ``developer_mode`` on, ``"hostel360_fixed_otp": "123456"`` in
site_config.json makes every code that value, so testers can sign in without an SMS
gateway. The setting is ignored whenever developer mode is off, so a key left behind in
a production site_config cannot weaken it.
"""

import hashlib
import hmac
import ipaddress
import json
import math
import secrets
import string
import time

import frappe
from frappe import _
from frappe.utils import cint, cstr

from hostel360.hostel_communication.events import MOBILE_CHANNELS, SMS, WHATSAPP, normalize_channel
from hostel360.utils.validation import mask_mobile, normalize_mobile

KIND_STUDENT = "student"
KIND_PARENT = "parent"
KIND_DOCTYPES = {KIND_STUDENT: "Hostel Student", KIND_PARENT: "Hostel Guardian"}

HOUR = 3600
FAILED_VERIFICATIONS_PER_HOUR = 10
FAILED_VERIFICATIONS_PER_IP_PER_HOUR = 100
LOCKOUT_SECONDS = HOUR
INACTIVE_STUDENT_STATUSES = ("Vacated", "Alumni")

# Unknown numbers are answered after roughly the time a real send takes.
DEFAULT_SEND_SECONDS = 0.4
MAX_EQUALISE_SECONDS = 3.0
SEND_TIME_KEY = "send_seconds"

_random = secrets.SystemRandom()


# ---------------------------------------------------------------- policy


def get_policy() -> frappe._dict:
	settings = frappe.get_cached_doc("Messaging Settings")
	return frappe._dict(
		length=min(max(cint(settings.get("otp_length")) or 6, 4), 8),
		expiry=min(max(cint(settings.get("otp_expiry_seconds")) or 300, 60), 1800),
		max_attempts=max(cint(settings.get("otp_max_attempts")) or 5, 1),
		cooldown=max(cint(settings.get("otp_resend_cooldown_seconds")) or 60, 1),
		sends_per_hour=max(cint(settings.get("otp_max_sends_per_hour")) or 5, 1),
		sends_per_ip=max(cint(settings.get("otp_max_sends_per_ip_per_hour")) or 20, 1),
		channels=allowed_channels(settings.get("otp_channels")),
		trusted_networks=parse_networks(settings.get("otp_trusted_networks")),
	)


def allowed_channels(setting: str | None) -> list[str]:
	if setting == "WhatsApp":
		return [WHATSAPP]
	if setting == "SMS and WhatsApp":
		return [SMS, WHATSAPP]
	return [SMS]


def parse_networks(text: str | None) -> list:
	networks = []
	for line in (text or "").splitlines():
		try:
			networks.append(ipaddress.ip_network(line.strip(), strict=False))
		except ValueError:
			continue
	return networks


def is_trusted_ip(ip: str | None, networks: list) -> bool:
	if not ip or not networks:
		return False
	try:
		address = ipaddress.ip_address(ip)
	except ValueError:
		return False
	return any(address in network for network in networks)


# ---------------------------------------------------------------- codes and keys


def _site_key(purpose: str) -> bytes:
	from frappe.utils.password import get_encryption_key

	return hmac.new(
		get_encryption_key().encode(), f"hostel360/{purpose}/v1".encode(), hashlib.sha256
	).digest()


def mobile_ref(mobile: str) -> str:
	return hmac.new(_site_key("otp-ref"), mobile.encode(), hashlib.sha256).hexdigest()[:32]


def ip_ref(ip: str) -> str:
	return hmac.new(_site_key("otp-ref"), f"ip:{ip}".encode(), hashlib.sha256).hexdigest()[:24]


def code_digest(mobile: str, code: str) -> str:
	return hmac.new(_site_key("otp"), f"{mobile}:{code}".encode(), hashlib.sha256).hexdigest()


def fixed_code() -> str | None:
	"""The development sign-in code; None unless developer mode is on (see module docstring)."""
	if not cint(frappe.conf.get("developer_mode")):
		return None
	value = cstr(frappe.conf.get("hostel360_fixed_otp")).strip()
	return value if value.isdigit() and 4 <= len(value) <= 8 else None


def generate_code(length: int) -> str:
	return fixed_code() or "".join(secrets.choice(string.digits) for _ in range(length))


def _now() -> float:
	return time.time()


def _sleep(seconds: float) -> None:
	time.sleep(seconds)


# ---------------------------------------------------------------- redis primitives


def _key(name: str) -> bytes:
	return frappe.cache.make_key(f"hostel360:otp:{name}")


def _incr_window(name: str, window: int) -> int:
	"""Increment a counter that resets ``window`` seconds after its first increment."""
	key = _key(name)
	pipe = frappe.cache.pipeline()
	pipe.incr(key)
	pipe.expire(key, window, nx=True)
	count, _ = pipe.execute()
	return cint(count)


def _count(name: str) -> int:
	return cint(frappe.cache.get(_key(name)))


def _ttl(name: str) -> int:
	return max(cint(frappe.cache.ttl(_key(name))), 0)


def _delete(*names: str) -> None:
	frappe.cache.delete(*[_key(name) for name in names])


def _store_code(ref: str, mobile: str, code: str, expiry: int, channel: str | None) -> None:
	state = {"d": code_digest(mobile, code), "e": _now() + expiry, "c": channel or ""}
	pipe = frappe.cache.pipeline()
	pipe.set(_key(f"code:{ref}"), json.dumps(state), ex=expiry)
	pipe.delete(_key(f"attempts:{ref}"))
	pipe.execute()


def _load_code(ref: str) -> dict | None:
	raw = frappe.cache.get(_key(f"code:{ref}"))
	if not raw:
		return None
	try:
		state = json.loads(raw)
	except ValueError:
		return None
	if not isinstance(state, dict) or not state.get("d") or float(state.get("e") or 0) < _now():
		return None
	return state


def _clear_code(ref: str) -> None:
	_delete(f"code:{ref}", f"attempts:{ref}")


# ---------------------------------------------------------------- identity


def portal_enabled(fieldname: str) -> bool:
	"""Hostel Settings portal switch; a switch that was never saved counts as on."""
	singles = frappe.qb.DocType("Singles")
	rows = (
		frappe.qb.from_(singles)
		.select(singles.value)
		.where(singles.doctype == "Hostel Settings")
		.where(singles.field == fieldname)
	).run(pluck=True)
	return not rows or rows[0] in (None, "") or cint(rows[0]) == 1


def resolve_identities(mobile: str) -> list[frappe._dict]:
	"""Portal identities that may sign in with this mobile number (student and/or parent)."""
	identities = []
	if portal_enabled("student_portal_enabled") and frappe.db.exists("DocType", "Hostel Student"):
		student = frappe.db.get_value(
			"Hostel Student",
			{"mobile_number": mobile},
			["name", "status", "validation_status"],
			as_dict=True,
		)
		if (
			student
			and student.status not in INACTIVE_STUDENT_STATUSES
			and student.validation_status != "Rejected"
		):
			identities.append(frappe._dict(kind=KIND_STUDENT, doctype="Hostel Student", name=student.name))

	if portal_enabled("parent_portal_enabled") and frappe.db.exists("DocType", "Hostel Guardian"):
		guardian = frappe.db.get_value(
			"Hostel Guardian",
			{"mobile_number": mobile},
			["name", "validation_status"],
			as_dict=True,
		)
		if (
			guardian
			and guardian.validation_status != "Rejected"
			and frappe.db.exists(
				"Hostel Student Guardian", {"guardian": guardian.name, "parenttype": "Hostel Student"}
			)
		):
			identities.append(frappe._dict(kind=KIND_PARENT, doctype="Hostel Guardian", name=guardian.name))
	return identities


# ---------------------------------------------------------------- sending


def request_code(mobile: str, channel: str | None = None, ip: str | None = None) -> dict:
	"""Send a sign-in code if the number belongs to a portal user. Same answer either way."""
	started = time.monotonic()
	mobile = _normalise(mobile)
	policy = get_policy()
	ref = mobile_ref(mobile)
	_check_lock(ref, mobile, ip)

	if ip and not is_trusted_ip(ip, policy.trusted_networks):
		if _incr_window(f"ip:{ip_ref(ip)}", HOUR) > policy.sends_per_ip:
			security_log("otp_ip_limit", mobile, ip)
			frappe.throw(
				_("Too many sign-in codes have been requested from your network. Please try again later."),
				frappe.RateLimitExceededError,
			)

	if not frappe.cache.set(_key(f"cooldown:{ref}"), "1", nx=True, ex=policy.cooldown):
		wait = _ttl(f"cooldown:{ref}") or policy.cooldown
		frappe.throw(
			_("Please wait {0} seconds before requesting another code.").format(wait),
			frappe.RateLimitExceededError,
		)

	if _incr_window(f"sends:{ref}", HOUR) > policy.sends_per_hour:
		security_log("otp_number_limit", mobile, ip)
		frappe.throw(
			_("Too many codes have been requested for this number. Please try again in an hour."),
			frappe.RateLimitExceededError,
		)

	identities = resolve_identities(mobile)
	if identities:
		# Named *_secret so Frappe's traceback sanitiser masks it if anything below fails.
		otp_secret = generate_code(policy.length)
		_store_code(ref, mobile, otp_secret, policy.expiry, channel)
		delivered = _deliver(mobile, otp_secret, channel, policy)
		del otp_secret
		security_log(
			"otp_sent" if delivered else "otp_send_failed", mobile, ip, kinds=[i.kind for i in identities]
		)
		_remember_send_time(time.monotonic() - started)
	else:
		security_log("otp_requested_unknown", mobile, ip)
		_equalise_timing(started)

	return {
		"sent": True,
		"masked": mask_mobile(mobile),
		"resend_after": policy.cooldown,
		"expires_in": policy.expiry,
	}


def _deliver(mobile: str, otp_secret: str, requested: str | None, policy) -> bool:
	"""Try the requested channel first, then any other channel allowed for codes."""
	from hostel360.hostel_communication.dispatch import send_otp_message

	requested = normalize_channel(requested)
	order = [requested] if requested in policy.channels else []
	order += [c for c in policy.channels if c not in order and c in MOBILE_CHANNELS]
	for channel in order:
		try:
			if send_otp_message(mobile, otp_secret, channel):
				return True
		except Exception:
			frappe.log_error(title="Hostel360: sign-in code delivery error", message=frappe.get_traceback())
	return False


def _send_time_key() -> bytes:
	return _key(SEND_TIME_KEY)


def _typical_send_seconds() -> float:
	try:
		value = float(frappe.cache.get(_send_time_key()) or DEFAULT_SEND_SECONDS)
	except (TypeError, ValueError):
		value = DEFAULT_SEND_SECONDS
	return min(max(value, 0.05), MAX_EQUALISE_SECONDS)


def _remember_send_time(seconds: float) -> None:
	# Exponentially weighted average, so the decoy delay follows the gateway's real latency.
	average = _typical_send_seconds() * 0.8 + min(seconds, MAX_EQUALISE_SECONDS) * 0.2
	frappe.cache.set(_send_time_key(), f"{average:.3f}", ex=7 * 24 * HOUR)


def _equalise_timing(started: float) -> None:
	target = _typical_send_seconds() * _random.uniform(0.85, 1.15)
	delay = target - (time.monotonic() - started)
	if delay > 0:
		_sleep(min(delay, MAX_EQUALISE_SECONDS))


# ---------------------------------------------------------------- verification


def verify_code(mobile: str, code: str, kind: str | None = None, ip: str | None = None) -> dict:
	"""Check a sign-in code.

	Returns ``{"ok": True, "kind", "doctype", "name"}`` and consumes the code, or
	``{"ok": False, "choose": [...]}`` without consuming it when the number belongs to both
	a student and a parent and ``kind`` was not given. Raises for wrong, expired or locked
	codes.
	"""
	mobile = _normalise(mobile)
	policy = get_policy()
	ref = mobile_ref(mobile)
	_check_lock(ref, mobile, ip)

	code = cstr(code).strip().replace(" ", "")
	if not code.isdigit() or not 4 <= len(code) <= 8:
		frappe.throw(_("Please enter the {0}-digit code sent to your phone.").format(policy.length))

	if ip and not is_trusted_ip(ip, policy.trusted_networks):
		if _count(f"ipfail:{ip_ref(ip)}") >= FAILED_VERIFICATIONS_PER_IP_PER_HOUR:
			security_log("otp_ip_verify_limit", mobile, ip)
			frappe.throw(
				_("Too many incorrect codes have been entered from your network. Please try again later."),
				frappe.RateLimitExceededError,
			)

	state = _load_code(ref)
	if not state:
		_register_failure(ref, mobile, ip, reason="no active code")
		frappe.throw(
			_("This code is not valid or has expired. Please request a new code."),
			frappe.AuthenticationError,
		)

	if not hmac.compare_digest(state["d"], code_digest(mobile, code)):
		attempts = _incr_window(f"attempts:{ref}", policy.expiry)
		_register_failure(ref, mobile, ip, reason="wrong code")
		if attempts >= policy.max_attempts:
			_clear_code(ref)
			frappe.throw(
				_("The code was entered incorrectly too many times. Please request a new code."),
				frappe.AuthenticationError,
			)
		left = policy.max_attempts - attempts
		frappe.throw(
			_("The code is incorrect. You have {0} more attempt left.").format(left)
			if left == 1
			else _("The code is incorrect. You have {0} more attempts left.").format(left),
			frappe.AuthenticationError,
		)

	identities = resolve_identities(mobile)
	if not identities:
		_clear_code(ref)
		security_log("otp_no_identity", mobile, ip)
		frappe.throw(
			_("We could not find an active hostel record for this number. Please contact the hostel office."),
			frappe.AuthenticationError,
		)

	kind = _normalise_kind(kind)
	if kind:
		identity = next((i for i in identities if i.kind == kind), None)
		if not identity:
			frappe.throw(
				_("This number is not registered for the {0} portal.").format(
					_("student") if kind == KIND_STUDENT else _("parent")
				),
				frappe.AuthenticationError,
			)
	elif len(identities) > 1:
		return {"ok": False, "choose": [i.kind for i in identities]}
	else:
		identity = identities[0]

	_clear_code(ref)
	_delete(f"fail:{ref}")
	security_log("otp_verified", mobile, ip, kind=identity.kind)
	return {"ok": True, "kind": identity.kind, "doctype": identity.doctype, "name": identity.name}


def _normalise_kind(kind: str | None) -> str | None:
	value = cstr(kind).strip().lower()
	if not value:
		return None
	if value in ("parent", "guardian"):
		return KIND_PARENT
	if value == KIND_STUDENT:
		return KIND_STUDENT
	frappe.throw(_("Choose whether you are signing in as a student or a parent."))


def _normalise(mobile: str) -> str:
	try:
		value = normalize_mobile(mobile)
	except frappe.ValidationError:
		frappe.clear_last_message()
		value = None
	if not value:
		frappe.throw(_("Please enter a valid 10-digit mobile number."))
	return value


def _check_lock(ref: str, mobile: str, ip: str | None) -> None:
	remaining = _ttl(f"lock:{ref}")
	if remaining:
		security_log("otp_locked_attempt", mobile, ip)
		frappe.throw(
			_(
				"For your security, sign-in for this number is paused after too many incorrect codes. "
				"Please try again in {0} minutes or contact the hostel office."
			).format(max(1, math.ceil(remaining / 60))),
			frappe.RateLimitExceededError,
		)


def _register_failure(ref: str, mobile: str, ip: str | None, reason: str) -> None:
	failures = _incr_window(f"fail:{ref}", HOUR)
	if ip:
		_incr_window(f"ipfail:{ip_ref(ip)}", HOUR)
	security_log("otp_verify_failed", mobile, ip, reason=reason, failures=failures)
	_record_failed_sign_in(mobile, reason)
	if failures >= FAILED_VERIFICATIONS_PER_HOUR:
		frappe.cache.set(_key(f"lock:{ref}"), "1", ex=LOCKOUT_SECONDS)
		_clear_code(ref)
		security_log("otp_number_locked", mobile, ip, failures=failures)


def _record_failed_sign_in(mobile: str, reason: str) -> None:
	"""Activity Log entry for the audit trail.

	Queued rather than inserted, because the failed request's transaction is rolled back
	when the error is raised to the caller.
	"""
	try:
		frappe.enqueue(
			"hostel360.auth.otp.write_failed_sign_in",
			queue="short",
			now=frappe.in_test,
			subject=f"Portal sign-in failed for {mask_mobile(mobile)} ({reason})",
		)
	except Exception:
		frappe.log_error(title="Hostel360: could not record failed sign-in", message=frappe.get_traceback())


def write_failed_sign_in(subject: str) -> None:
	from frappe.core.doctype.activity_log.activity_log import add_authentication_log

	add_authentication_log(subject, "Guest", operation="Login", status="Failed")


def security_log(event: str, mobile: str | None = None, ip: str | None = None, **extra) -> None:
	"""One line per security event in hostel360_security.log (masked; never the code)."""
	from hostel360.security.audit import security_log as write

	if "kinds" in extra:
		extra["kinds"] = ",".join(extra["kinds"])
	write(event, mobile=mobile, ip=ip, **extra)
