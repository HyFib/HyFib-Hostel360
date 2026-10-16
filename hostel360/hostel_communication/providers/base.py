"""Plumbing shared by the SMS, WhatsApp and e-mail providers.

Providers never raise for delivery problems: they return a ``DeliveryResult`` that says
whether the message went out, whether trying again later could help, and what the
provider said. Secrets (auth keys, tokens) are never included in errors or logs.
"""

from dataclasses import dataclass

import frappe
from frappe.utils import cint, cstr

DEFAULT_TIMEOUT = 10
CONNECT_TIMEOUT = 5
ERROR_TEXT_LIMIT = 500
FAILURE_COUNTER_TTL = 6 * 60 * 60
# How long sign-in codes skip a provider that keeps failing before one is let through again.
BREAKER_SECONDS = 60
ALERT_METHOD = "hostel360.hostel_communication.providers.base.raise_provider_alert"
RESOLVE_METHOD = "hostel360.hostel_communication.providers.base.resolve_provider_alert"

# HTTP statuses that are worth retrying later; everything else in 4xx is a request or
# configuration problem that will fail the same way again.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


@dataclass
class DeliveryResult:
	ok: bool
	provider: str
	message_id: str | None = None
	error: str | None = None
	retryable: bool = False
	skipped: bool = False

	@classmethod
	def sent(cls, provider: str, message_id: str | None = None) -> "DeliveryResult":
		return cls(ok=True, provider=provider, message_id=message_id)

	@classmethod
	def failed(cls, provider: str, error: str, retryable: bool = False) -> "DeliveryResult":
		return cls(ok=False, provider=provider, error=truncate(error), retryable=retryable)

	@classmethod
	def skip(cls, provider: str, reason: str, ok: bool = False) -> "DeliveryResult":
		return cls(ok=ok, provider=provider, error=reason, skipped=True)


def truncate(text, limit: int = ERROR_TEXT_LIMIT) -> str:
	text = cstr(text).strip()
	return text if len(text) <= limit else text[: limit - 3] + "..."


def get_settings():
	return frappe.get_cached_doc("Messaging Settings")


def get_secret(settings, fieldname: str) -> str | None:
	try:
		return settings.get_password(fieldname, raise_exception=False)
	except Exception:
		return None


def get_timeout(settings) -> tuple[int, int]:
	read_timeout = cint(settings.get("provider_timeout_seconds")) or DEFAULT_TIMEOUT
	return (CONNECT_TIMEOUT, max(2, min(read_timeout, 60)))


def is_test_mode(settings) -> bool:
	return bool(cint(settings.get("test_mode")))


def test_mode_result(provider: str) -> DeliveryResult:
	return DeliveryResult(
		ok=True,
		provider=provider,
		message_id="test-mode",
		error="Test mode is on: the message was logged but not sent.",
		skipped=True,
	)


def demo_mode_result(provider: str) -> DeliveryResult | None:
	"""On a demonstration site nothing is ever handed to a provider (see ``hostel360.demo``).

	Checked before anything else a provider does, so no setting a desk user can change
	(enabling a channel, entering real gateway keys, switching test mode off) lets a message
	out. The Message Log row says Skipped, with the reason.
	"""
	from hostel360.demo import DEMO_REASON, is_demo_site

	if not is_demo_site():
		return None
	return DeliveryResult(ok=True, provider=provider, error=DEMO_REASON, skipped=True)


def http_failure(provider: str, response) -> DeliveryResult:
	status = response.status_code
	return DeliveryResult.failed(
		provider,
		f"HTTP {status}: {truncate(response.text, 300)}",
		retryable=status in RETRYABLE_STATUS or status >= 500,
	)


def request_failure(provider: str, exc: Exception) -> DeliveryResult:
	"""Network problems (timeouts, DNS, TLS) are always worth another try."""
	return DeliveryResult.failed(provider, f"{type(exc).__name__}: {truncate(exc, 300)}", retryable=True)


def json_or_empty(response) -> dict:
	try:
		data = response.json()
	except ValueError:
		return {}
	return data if isinstance(data, dict) else {"data": data}


# ---------------------------------------------------------------- failure tracking


def _failure_key(provider: str) -> bytes:
	return frappe.cache.make_key(f"hostel360:messaging:consecutive_failures:{provider}")


def _breaker_key(provider: str) -> bytes:
	return frappe.cache.make_key(f"hostel360:messaging:breaker:{provider}")


def _probe_key(provider: str) -> bytes:
	return frappe.cache.make_key(f"hostel360:messaging:breaker_probe:{provider}")


def _slug(provider: str) -> str:
	return "".join(ch if ch.isalnum() else "-" for ch in provider.lower())


def _alert_key(provider: str) -> str:
	return f"messaging-{_slug(provider)}-failing"


def record_outcome(result: DeliveryResult, *, allow_alert: bool = True) -> None:
	"""Track consecutive provider failures and tell HyFib operations when a provider breaks.

	Skipped messages (test mode, missing address) say nothing about provider health and
	are ignored. ``allow_alert`` is False for messages that are themselves operations
	alerts, so a broken channel cannot trigger an alert storm about itself.

	Retryable failures (timeouts, 5xx, throttling) past the alert threshold also open the
	provider's circuit breaker (see ``provider_paused``). Alerts are raised and resolved in
	a job of their own, after the current transaction commits: a delivery job holds its
	transaction across slow provider calls, and creating an Ops Alert inside it would hold
	the alert series lock for as long, blocking every other alert on the site.
	"""
	if result.skipped or not result.provider:
		return
	key = _failure_key(result.provider)
	try:
		if result.ok:
			previous = cint(frappe.cache.get(key))
			if previous:
				frappe.cache.delete(key, _breaker_key(result.provider), _probe_key(result.provider))
				if previous >= _threshold():
					_queue(
						RESOLVE_METHOD,
						f"hostel360-messaging-resolve-{_slug(result.provider)}",
						provider=result.provider,
					)
			return

		count = cint(frappe.cache.incr(key))
		frappe.cache.expire(key, FAILURE_COUNTER_TTL)
	except Exception:
		# Redis trouble must never stop a message from being logged.
		return

	threshold = _threshold()
	if count >= threshold and result.retryable:
		_open_breaker(result.provider)
	if allow_alert and count >= threshold and (count - threshold) % threshold == 0:
		_queue(
			ALERT_METHOD,
			f"hostel360-messaging-alert-{_slug(result.provider)}",
			provider=result.provider,
			count=count,
			threshold=threshold,
			error=truncate(result.error, 300),
		)


def provider_paused(provider: str) -> bool:
	"""Should a time-critical send (a sign-in code) skip this provider right now?

	True while the provider's breaker is open, except for one caller per
	``BREAKER_SECONDS`` window, which goes through as a probe. Any successful send, the
	probe's or a background message's, closes the breaker.
	"""
	if not provider:
		return False
	try:
		if not frappe.cache.get(_breaker_key(provider)):
			return False
		return not frappe.cache.set(_probe_key(provider), "1", nx=True, ex=BREAKER_SECONDS)
	except Exception:
		return False


def _open_breaker(provider: str) -> None:
	try:
		frappe.cache.set(_breaker_key(provider), "1", ex=BREAKER_SECONDS)
	except Exception:
		# Without Redis the breaker stays closed: codes are still tried, within their timeout.
		frappe.logger("hostel360").warning(f"Could not pause {provider} for sign-in codes", exc_info=True)


def _threshold() -> int:
	try:
		value = cint(get_settings().get("failure_alert_threshold"))
	except Exception:
		value = 0
	return max(value or 5, 1)


def _queue(method: str, job_id: str, **kwargs) -> None:
	try:
		frappe.enqueue(
			method,
			queue="short",
			job_id=job_id,
			deduplicate=not frappe.in_test,
			enqueue_after_commit=not frappe.in_test,
			now=frappe.in_test,
			**kwargs,
		)
	except Exception:
		frappe.log_error(title="Hostel360: could not queue messaging alert", message=frappe.get_traceback())


def raise_provider_alert(provider: str, count: int, threshold: int, error: str | None = None) -> None:
	"""Background job: open (or bump) the Ops Alert for a failing provider."""
	if frappe.flags.hostel360_raising_messaging_alert:
		return
	try:
		from hostel360.hostel_operations.alerts import raise_alert
	except ImportError:
		frappe.logger("hostel360").warning(f"{provider} has failed {count} times in a row")
		return

	frappe.flags.hostel360_raising_messaging_alert = True
	try:
		raise_alert(
			key=_alert_key(provider),
			title=f"{provider} messages are failing",
			severity="Critical" if cint(count) >= cint(threshold) * 4 else "Warning",
			category="Messaging",
			source="App",
			description=(
				f"The last {count} attempts to send through {provider} failed. "
				"Students and parents may not be receiving notifications or sign-in codes."
			),
			details={"provider": provider, "consecutive_failures": count, "last_error": truncate(error, 300)},
		)
	except frappe.QueryDeadlockError:
		# raise_alert had to write in this job's own transaction, and InnoDB has rolled all of
		# it back. Carrying on would commit only the work that follows.
		raise
	except Exception:
		frappe.log_error(title="Hostel360: could not raise messaging alert", message=frappe.get_traceback())
	finally:
		frappe.flags.hostel360_raising_messaging_alert = False


def resolve_provider_alert(provider: str) -> None:
	"""Background job: close the Ops Alert once the provider delivers again."""
	try:
		from hostel360.hostel_operations.alerts import resolve_alert
	except ImportError:
		return
	try:
		resolve_alert(_alert_key(provider), notes=f"{provider} is delivering messages again.", source="App")
	except frappe.QueryDeadlockError:
		# As in raise_provider_alert: the transaction is gone, so the job must fail.
		raise
	except Exception:
		frappe.log_error(title="Hostel360: could not resolve messaging alert", message=frappe.get_traceback())
