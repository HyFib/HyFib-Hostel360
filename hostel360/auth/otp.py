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
* per number: ``FAILED_VERIFICATIONS_PER_HOUR`` wrong guesses against a live code lock the
  number for an hour.

Each guess reserves its attempt (and its place in the failure counts) before it is
compared, so a burst of simultaneous guesses gets no more comparisons than the limits
allow.

Only students whose registration the hostel office has validated, and who have not left,
may sign in, and parents only while they have such a ward (tender 3.1 and 4.1). Requests
for any other number - unknown, not yet validated, closed - get exactly the same response,
take about as long as a real send, store a code nobody receives (so verification answers
the same way too) and count against the same limits, so the endpoints cannot be used to
find out who lives in the hostel.

Codes go out synchronously, inside the sign-in request, so the send is bounded: a short
provider timeout and an overall time budget, a limit on sends in flight across all
workers, and a circuit breaker that stops calling a provider that keeps failing (see
``hostel360.hostel_communication.providers.base``). A messaging outage then costs a few
seconds per request at most instead of tying up the web workers.

E-mail codes (when Messaging Settings allows them) work the same way. The mobile number is
still what identifies the person; the code goes to the e-mail address on their student or
guardian record, never to a portal placeholder address. A number with no address on record
is answered exactly like an unknown one, with the delay of a real e-mail. An e-mailed code
signs in only the records registered with the address it went to (see ``email_recipient``).
An e-mail that cannot be sent is not replaced by an SMS, nor an SMS by an e-mail: the person
waits for the code where they asked for it. (While e-mail codes are switched off, a request
for one is treated like any other channel that is not allowed: it goes by the mobile
channels.)

Development shortcut: with ``developer_mode`` on, ``"hostel360_fixed_otp": "123456"`` in
site_config.json makes every code that value, so testers can sign in without an SMS
gateway. The setting is ignored whenever developer mode is off, so a key left behind in
a production site_config cannot weaken it.

Demonstration sites (``hostel360.demo``) work the same way with the published demo code,
and only while ``hostel360_demo_mode`` is on. The code still signs in only numbers that
belong to records on the site, every limit above still applies, and the providers send
nothing. Because the code is the same for every visitor, a visitor may enter it without
asking for a code first: several people trying the same demo account at once would
otherwise wait on each other's resend cooldown.
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
from frappe.utils import cint, cstr, validate_email_address

from hostel360.hostel_communication.events import (
	EMAIL,
	MOBILE_CHANNELS,
	SMS,
	WHATSAPP,
	normalize_channel,
	normalize_language,
)
from hostel360.utils.validation import mask_mobile, normalize_mobile

KIND_STUDENT = "student"
KIND_PARENT = "parent"
KIND_DOCTYPES = {KIND_STUDENT: "Hostel Student", KIND_PARENT: "Hostel Guardian"}

HOUR = 3600
FAILED_VERIFICATIONS_PER_HOUR = 10
FAILED_VERIFICATIONS_PER_IP_PER_HOUR = 100
LOCKOUT_SECONDS = HOUR
INACTIVE_STUDENT_STATUSES = ("Vacated", "Alumni")
VALIDATED = "Validated"
REJECTED = "Rejected"

# Unknown numbers are answered after roughly the time a real send takes, measured
# separately for e-mail: an SMTP conversation takes longer than an SMS gateway call.
DEFAULT_SEND_SECONDS = 0.4
DEFAULT_EMAIL_SEND_SECONDS = 1.0
MAX_EQUALISE_SECONDS = 3.0
SEND_TIME_KEY = "send_seconds"
EMAIL_SEND_TIME_KEY = "send_seconds:email"

# Bounds on the synchronous send (seconds). Background notifications keep the longer
# provider timeout from Messaging Settings.
OTP_CONNECT_TIMEOUT = 2.0
OTP_READ_TIMEOUT = 3.0
OTP_SEND_BUDGET_SECONDS = 4.0
MIN_CHANNEL_SECONDS = 1.0
# Sign-in codes being handed to a provider at the same moment, across all web workers.
MAX_SENDS_IN_FLIGHT = 6
SEND_SLOT_SECONDS = 30

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
		channels=[
			*allowed_channels(settings.get("otp_channels")),
			*([EMAIL] if email_codes_enabled(settings) else []),
		],
		trusted_networks=parse_networks(settings.get("otp_trusted_networks")),
	)


def allowed_channels(setting: str | None) -> list[str]:
	"""Mobile channels for codes (the ``otp_channels`` setting)."""
	if setting == "WhatsApp":
		return [WHATSAPP]
	if setting == "SMS and WhatsApp":
		return [SMS, WHATSAPP]
	return [SMS]


def email_codes_enabled(settings=None) -> bool:
	"""May students and parents ask for their code by e-mail?

	A site-wide switch, safe to tell anyone: it says nothing about any number. Codes are
	e-mailed only while e-mail itself is enabled in Messaging Settings.
	"""
	settings = settings or frappe.get_cached_doc("Messaging Settings")
	return bool(cint(settings.get("otp_email_enabled")) and cint(settings.get("email_enabled")))


def delivery_route(requested: str | None, policy) -> list[str]:
	"""Channels to try, in order, for the channel the person asked for.

	E-mail is tried alone. A channel that is not allowed (e-mail while it is switched off,
	WhatsApp on an SMS-only site) gets the allowed mobile channels, as before.
	"""
	requested = normalize_channel(requested)
	if requested == EMAIL and EMAIL in policy.channels:
		return [EMAIL]
	mobile = [c for c in policy.channels if c in MOBILE_CHANNELS] or [SMS]
	return ([requested] if requested in mobile else []) + [c for c in mobile if c != requested]


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
	"""The published demo code on a demonstration site, the development code with developer
	mode on, otherwise None (see module docstring)."""
	from hostel360.demo import demo_otp, is_demo_site

	if is_demo_site():
		return demo_otp()
	if not cint(frappe.conf.get("developer_mode")):
		return None
	value = cstr(frappe.conf.get("hostel360_fixed_otp")).strip()
	return value if value.isdigit() and 4 <= len(value) <= 8 else None


def _demo_code_state(mobile: str, expiry: int) -> dict | None:
	"""On a demonstration site, the demo code is live for every number on record.

	Only numbers that belong to a portal record get it; any other number has nothing to
	guess against, exactly as on a production site.
	"""
	from hostel360.demo import is_demo_site

	if not is_demo_site() or not resolve_identities(mobile):
		return None
	return {"d": code_digest(mobile, fixed_code()), "e": _now() + expiry, "c": ""}


def generate_code(length: int) -> str:
	return fixed_code() or "".join(secrets.choice(string.digits) for _ in range(length))


def _now() -> float:
	return time.time()


def _clock() -> float:
	return time.monotonic()


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


def _store_code(
	ref: str, mobile: str, code: str, expiry: int, channel: str | None, kinds: list[str] | None = None
) -> None:
	"""Keep the code's digest. ``kinds`` limits whom the code signs in (e-mailed codes);
	without it, any portal record holding the number."""
	state = {"d": code_digest(mobile, code), "e": _now() + expiry, "c": channel or ""}
	if kinds is not None:
		state["k"] = list(kinds)
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


def student_may_sign_in(student) -> bool:
	"""Validated against the college register and still a resident (tender 3.1)."""
	return bool(
		student
		and student.get("validation_status") == VALIDATED
		and student.get("status") not in INACTIVE_STUDENT_STATUSES
	)


def guardian_has_portal_ward(guardian: str) -> bool:
	"""The parent portal opens only for a guardian of at least one validated resident (4.1)."""
	if not guardian:
		return False
	link = frappe.qb.DocType("Hostel Student Guardian")
	student = frappe.qb.DocType("Hostel Student")
	rows = (
		frappe.qb.from_(link)
		.join(student)
		.on(student.name == link.parent)
		.select(student.name)
		.where(link.guardian == guardian)
		.where(link.parenttype == "Hostel Student")
		.where(student.validation_status == VALIDATED)
		.where(student.status.notin(list(INACTIVE_STUDENT_STATUSES)))
		.limit(1)
	).run(pluck=True)
	return bool(rows)


def resolve_identities(mobile: str) -> list[frappe._dict]:
	"""Portal identities that may sign in with this mobile number (student and/or parent).

	Each carries ``kind``, ``doctype``, ``name``, ``display_name`` and ``email``, the real
	e-mail address on the record (None when there is none, or only a placeholder).
	"""
	identities = []
	if portal_enabled("student_portal_enabled") and frappe.db.exists("DocType", "Hostel Student"):
		student = frappe.db.get_value(
			"Hostel Student",
			{"mobile_number": mobile},
			["name", "status", "validation_status", "email", "full_name"],
			as_dict=True,
		)
		if student_may_sign_in(student):
			identities.append(
				frappe._dict(
					kind=KIND_STUDENT,
					doctype="Hostel Student",
					name=student.name,
					display_name=student.full_name,
					email=registered_email(student.email),
				)
			)

	if portal_enabled("parent_portal_enabled") and frappe.db.exists("DocType", "Hostel Guardian"):
		guardian = frappe.db.get_value(
			"Hostel Guardian",
			{"mobile_number": mobile},
			["name", "validation_status", "email", "guardian_name"],
			as_dict=True,
		)
		if guardian and guardian.validation_status != REJECTED and guardian_has_portal_ward(guardian.name):
			identities.append(
				frappe._dict(
					kind=KIND_PARENT,
					doctype="Hostel Guardian",
					name=guardian.name,
					display_name=guardian.guardian_name,
					email=registered_email(guardian.email),
				)
			)
	return identities


def registered_email(value: str | None) -> str | None:
	"""A usable e-mail address from a student or guardian record.

	Portal accounts of people without an e-mail address get placeholder logins on the
	``.invalid`` TLD; such an address, should one ever be copied onto a record, is no address.
	"""
	from hostel360.hostel_communication.providers.email import is_placeholder_address

	address = cstr(value).strip().lower()
	if not address or is_placeholder_address(address) or not validate_email_address(address):
		return None
	return address


def email_recipient(identities: list) -> frappe._dict | None:
	"""Where an e-mailed code goes, and whom it may sign in: ``{email, kinds, name}``.

	The first identity with an address on record (the student before the parent) decides
	the address, and the code then signs in only the identities registered with that same
	address. A student who shares a parent's mobile number but has an e-mail address of
	their own therefore cannot sign in as the parent with an e-mailed code. None when no
	identity has an address.
	"""
	address = next((i.get("email") for i in identities if i.get("email")), None)
	if not address:
		return None
	holders = [i for i in identities if i.get("email") == address]
	return frappe._dict(
		email=address,
		kinds=[i.kind for i in holders],
		# Greet by name only when the address belongs to one person.
		name=holders[0].get("display_name") if len(holders) == 1 else None,
	)


# ---------------------------------------------------------------- sending


def request_code(
	mobile: str, channel: str | None = None, ip: str | None = None, language: str | None = None
) -> dict:
	"""Send a sign-in code if the number belongs to a portal user. Same answer either way.

	``channel`` is "sms", "whatsapp" or "email"; for e-mail the number is still looked up
	the same way and the code goes to the address on that record. ``language`` is the
	language on the requesting device (the portal sends it with every call); the code goes
	out in Tamil when the Tamil sign-in template is enabled.
	"""
	started = _clock()
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

	route = delivery_route(channel, policy)
	identities = resolve_identities(mobile)
	recipient = None
	if identities and route[0] == EMAIL:
		recipient = email_recipient(identities)
		if not recipient:
			# Nobody to e-mail: answered like an unknown number. The log line lets the office
			# see why a code never arrived.
			security_log("otp_email_unavailable", mobile, ip, kinds=[i.kind for i in identities])

	if identities and (recipient or route[0] != EMAIL):
		# Named *_secret so Frappe's traceback sanitiser masks it if anything below fails.
		otp_secret = generate_code(policy.length)
		kinds = recipient.kinds if recipient else None
		_store_code(ref, mobile, otp_secret, policy.expiry, route[0], kinds=kinds)
		language = normalize_language(language) if language else request_language()
		delivered = _deliver(
			mobile, otp_secret, route, policy, started, language=language, recipient=recipient
		)
		del otp_secret
		security_log(
			"otp_sent" if delivered else "otp_send_failed",
			mobile,
			ip,
			kinds=kinds or [i.kind for i in identities],
			channel=route[0],
		)
		_remember_send_time(_clock() - started, route[0])
	else:
		# A code nobody receives, so verify_code answers exactly as it does for a registered
		# number: attempt counting, "attempts left", invalidation and lockout. It is hex, and
		# verify_code accepts digits only, so it can never be entered.
		_store_code(ref, mobile, secrets.token_hex(16), policy.expiry, route[0])
		if not identities:
			security_log("otp_requested_unknown", mobile, ip)
		_equalise_timing(started, route[0])

	return {
		"sent": True,
		"masked": mask_mobile(mobile),
		"resend_after": policy.cooldown,
		"expires_in": policy.expiry,
	}


def request_language() -> str:
	"""Language of the current request ("ta" or "en"), as Frappe resolved it from ``_lang``."""
	return normalize_language(getattr(frappe.local, "lang", None))


def _deliver(
	mobile: str,
	otp_secret: str,
	route: list[str],
	policy,
	started: float,
	language: str | None = None,
	recipient: frappe._dict | None = None,
) -> bool:
	"""Try the channels of ``route`` in turn until one accepts the code.

	The whole attempt stays within ``OTP_SEND_BUDGET_SECONDS`` of the request starting, and
	is skipped when too many codes are already on their way to a provider. E-mail goes to
	``recipient`` (see ``email_recipient``).
	"""
	from hostel360.hostel_communication.dispatch import send_otp_email, send_otp_message

	slot = _acquire_send_slot()
	if slot is None:
		security_log("otp_send_capacity", mobile)
		return False
	try:
		deadline = started + OTP_SEND_BUDGET_SECONDS
		for channel in route:
			remaining = deadline - _clock()
			if remaining < MIN_CHANNEL_SECONDS:
				break
			timeout = (min(OTP_CONNECT_TIMEOUT, remaining), min(OTP_READ_TIMEOUT, remaining))
			try:
				if channel == EMAIL:
					sent = bool(recipient) and send_otp_email(
						recipient.email, otp_secret, timeout=timeout, language=language, name=recipient.name
					)
				else:
					sent = send_otp_message(mobile, otp_secret, channel, timeout=timeout, language=language)
				if sent:
					return True
			except Exception:
				frappe.log_error(
					title="Hostel360: sign-in code delivery error", message=frappe.get_traceback()
				)
		return False
	finally:
		_release_send_slot(slot)


def _acquire_send_slot() -> str | None:
	"""A place among the codes in flight, or None when ``MAX_SENDS_IN_FLIGHT`` are out.

	A sorted set of tokens scored by start time: entries older than ``SEND_SLOT_SECONDS``
	(a worker that died mid-send) drop out on their own.
	"""
	key = _key("inflight")
	token = secrets.token_hex(8)
	now = _now()
	try:
		pipe = frappe.cache.pipeline()
		pipe.zremrangebyscore(key, 0, now - SEND_SLOT_SECONDS)
		pipe.zadd(key, {token: now})
		pipe.zcard(key)
		pipe.expire(key, SEND_SLOT_SECONDS)
		in_flight = cint(pipe.execute()[2])
	except Exception:
		# The code itself lives in Redis, so this is no worse than the send failing later.
		return token
	if in_flight > MAX_SENDS_IN_FLIGHT:
		_release_send_slot(token)
		return None
	return token


def _release_send_slot(token: str | None) -> None:
	if not token:
		return
	try:
		frappe.cache.zrem(_key("inflight"), token)
	except Exception:
		# The slot ages out after SEND_SLOT_SECONDS anyway.
		frappe.logger("hostel360").warning("Could not release a sign-in code send slot", exc_info=True)


def _send_time_key(channel: str | None = None) -> bytes:
	"""Where the typical send time of a route is kept: one for e-mail, one for the mobile channels."""
	return _key(EMAIL_SEND_TIME_KEY if channel == EMAIL else SEND_TIME_KEY)


def _typical_send_seconds(channel: str | None = None) -> float:
	default = DEFAULT_EMAIL_SEND_SECONDS if channel == EMAIL else DEFAULT_SEND_SECONDS
	try:
		value = float(frappe.cache.get(_send_time_key(channel)) or default)
	except TypeError, ValueError:
		value = default
	return min(max(value, 0.05), MAX_EQUALISE_SECONDS)


def _remember_send_time(seconds: float, channel: str | None = None) -> None:
	# Exponentially weighted average, so the decoy delay follows the gateway's real latency.
	average = _typical_send_seconds(channel) * 0.8 + min(seconds, MAX_EQUALISE_SECONDS) * 0.2
	frappe.cache.set(_send_time_key(channel), f"{average:.3f}", ex=7 * 24 * HOUR)


def _equalise_timing(started: float, channel: str | None = None) -> None:
	target = _typical_send_seconds(channel) * _random.uniform(0.85, 1.15)
	delay = target - (_clock() - started)
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
	kind = _normalise_kind(kind)

	limit_ip = bool(ip) and not is_trusted_ip(ip, policy.trusted_networks)
	if limit_ip and _count(f"ipfail:{ip_ref(ip)}") >= FAILED_VERIFICATIONS_PER_IP_PER_HOUR:
		_refuse_network(mobile, ip)

	state = _load_code(ref) or _demo_code_state(mobile, policy.expiry)
	if not state:
		# Nothing to guess against: count it for the network, not against the number, or
		# anyone could keep a resident locked out without a single code being sent.
		_register_failure_without_code(ref, mobile, ip)
		frappe.throw(
			_("This code is not valid or has expired. Please request a new code."),
			frappe.AuthenticationError,
		)

	# Reserve this guess before comparing it. The counters are the gate: simultaneous
	# guesses each get their own number, and those past a limit are refused uncompared.
	attempts = _incr_window(f"attempts:{ref}", policy.expiry)
	failures = _incr_window(f"fail:{ref}", HOUR)
	ip_failures = _incr_window(f"ipfail:{ip_ref(ip)}", HOUR) if ip else 0
	if failures > FAILED_VERIFICATIONS_PER_HOUR:
		_lock_number(ref, mobile, ip, failures)
		_check_lock(ref, mobile, ip)
	if limit_ip and ip_failures > FAILED_VERIFICATIONS_PER_IP_PER_HOUR:
		_refuse_network(mobile, ip)
	if attempts > policy.max_attempts:
		_clear_code(ref)
		_too_many_attempts()

	if not hmac.compare_digest(state["d"], code_digest(mobile, code)):
		security_log("otp_verify_failed", mobile, ip, reason="wrong code", failures=failures)
		_record_failed_sign_in(mobile, "wrong code")
		if failures >= FAILED_VERIFICATIONS_PER_HOUR:
			_lock_number(ref, mobile, ip, failures)
		if attempts >= policy.max_attempts:
			_clear_code(ref)
			_too_many_attempts()
		left = policy.max_attempts - attempts
		frappe.throw(
			_("The code is incorrect. You have {0} more attempt left.").format(left)
			if left == 1
			else _("The code is incorrect. You have {0} more attempts left.").format(left),
			frappe.AuthenticationError,
		)

	# The right code: the reserved failure was not one.
	_decrement(f"fail:{ref}")
	if ip:
		_decrement(f"ipfail:{ip_ref(ip)}")

	registered = resolve_identities(mobile)
	# An e-mailed code signs in only the records registered with the address it went to.
	bound_kinds = state.get("k")
	identities = (
		[i for i in registered if i.kind in bound_kinds] if isinstance(bound_kinds, list) else registered
	)
	if not identities:
		_clear_code(ref)
		security_log("otp_no_identity", mobile, ip)
		frappe.throw(
			_("We could not find an active hostel record for this number. Please contact the hostel office."),
			frappe.AuthenticationError,
		)

	if kind:
		identity = next((i for i in identities if i.kind == kind), None)
		if not identity:
			# The right code with the wrong portal chosen is not a guess.
			_decrement(f"attempts:{ref}")
			if any(i.kind == kind for i in registered):
				frappe.throw(
					_(
						"The code sent by e-mail signs in to the {0} portal only. To sign in to the {1} "
						"portal, request a code to your mobile number instead."
					).format(_portal_label(identities[0].kind), _portal_label(kind)),
					frappe.AuthenticationError,
				)
			frappe.throw(
				_("This number is not registered for the {0} portal.").format(_portal_label(kind)),
				frappe.AuthenticationError,
			)
	elif len(identities) > 1:
		# The code stays valid for the second call, and asking does not use up an attempt.
		_decrement(f"attempts:{ref}")
		return {"ok": False, "choose": [i.kind for i in identities]}
	else:
		identity = identities[0]

	_clear_code(ref)
	_delete(f"fail:{ref}")
	security_log("otp_verified", mobile, ip, kind=identity.kind)
	return {"ok": True, "kind": identity.kind, "doctype": identity.doctype, "name": identity.name}


def _portal_label(kind: str) -> str:
	return _("student") if kind == KIND_STUDENT else _("parent")


def _normalise_kind(kind: str | None) -> str | None:
	value = cstr(kind).strip().lower()
	if not value:
		return None
	if value in ("parent", "guardian"):
		return KIND_PARENT
	if value == KIND_STUDENT:
		return KIND_STUDENT
	frappe.throw(_("Choose whether you are signing in as a student or a parent."))


def normalised_number(mobile: str) -> str:
	"""The 10-digit form sign-in keys everything on (friendly error when it is not a mobile)."""
	return _normalise(mobile)


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


def _register_failure_without_code(ref: str, mobile: str, ip: str | None) -> None:
	if ip:
		_incr_window(f"ipfail:{ip_ref(ip)}", HOUR)
	security_log("otp_verify_failed", mobile, ip, reason="no active code", failures=_count(f"fail:{ref}"))
	_record_failed_sign_in(mobile, "no active code")


def _lock_number(ref: str, mobile: str, ip: str | None, failures: int) -> None:
	if frappe.cache.set(_key(f"lock:{ref}"), "1", ex=LOCKOUT_SECONDS, nx=True):
		security_log("otp_number_locked", mobile, ip, failures=failures)
	_clear_code(ref)


def _refuse_network(mobile: str, ip: str | None) -> None:
	security_log("otp_ip_verify_limit", mobile, ip)
	frappe.throw(
		_("Too many incorrect codes have been entered from your network. Please try again later."),
		frappe.RateLimitExceededError,
	)


def _too_many_attempts() -> None:
	frappe.throw(
		_("The code was entered incorrectly too many times. Please request a new code."),
		frappe.AuthenticationError,
	)


def _decrement(name: str) -> None:
	key = _key(name)
	try:
		if cint(frappe.cache.decr(key)) <= 0:
			frappe.cache.delete(key)
	except Exception:
		# A counter left one too high only makes a limit apply slightly early.
		frappe.logger("hostel360").warning("Could not adjust a sign-in counter", exc_info=True)


def unlock_number(mobile: str) -> None:
	"""Lift a sign-in lock on a number (for the hostel office, after checking with the owner)."""
	ref = mobile_ref(_normalise(mobile))
	_delete(f"lock:{ref}", f"fail:{ref}")


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
