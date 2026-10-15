"""Ops alert lifecycle: raise, de-duplicate, acknowledge, suppress and resolve.

Any part of Hostel360 (and the host agent, OCI Monitoring and the uptime probe through the
ops API) reports a problem with ``raise_alert``. A stable ``key`` identifies the problem
("health.redis.cache", "backup.daily.failed"); while an alert with that key is open, further
occurrences only bump its counters, so a check failing every five minutes produces one
alert, not hundreds.

``raise_alert`` and ``resolve_alert`` write the alert in a short transaction of their own
(see ``transaction.independent``), serialised per key by a database named lock. The alert is
therefore recorded even when the caller's request fails afterwards, and a collision between
two callers raising the same key can never roll back either caller's own work. Neither
function raises: an alerting fault must not turn into a second failure for the caller, so
problems are written to the Error Log instead.
"""

import hashlib
import json
import random
import time
from functools import partial

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from hostel360.hostel_operations import notification, transaction
from hostel360.hostel_operations.constants import (
	ACTIVE_STATUSES,
	ALERT_DOCTYPE,
	CATEGORIES,
	REOPEN_WINDOW_MINUTES,
	SEVERITIES,
	SOURCES,
	STATUS_ACKNOWLEDGED,
	STATUS_OPEN,
	STATUS_RESOLVED,
	STATUS_SUPPRESSED,
	WARNING,
	severity_rank,
)
from hostel360.hostel_operations.settings import get_settings

MAX_KEY_LENGTH = 140
MAX_TITLE_LENGTH = 140
MAX_DESCRIPTION_LENGTH = 10_000
MAX_DETAILS_LENGTH = 60_000
SAVEPOINT = "h360_ops_alert"
RETRY_SAVEPOINT = "h360_ops_alert_retry"
# Deadlocks and "record has changed since last read" are retried on the independent
# connection, where nothing but the alert itself is lost by rolling back.
MAX_ATTEMPTS = 4


class AlertNotRecorded(Exception):
	"""The alert transaction gave up (lock not obtained, or repeated deadlocks)."""


def raise_alert(
	key,
	title,
	severity,
	category,
	source="App",
	description="",
	details=None,
	is_outage=False,
) -> str:
	"""Open an alert, or record another occurrence of the open alert with this key.

	Returns the Ops Alert name, or an empty string if the alert could not be recorded.
	"""
	work = partial(_raise_alert, key, title, severity, category, source, description, details, is_outage)
	return _execute(work, key, "Hostel360: could not raise ops alert", default="")


def resolve_alert(key, notes="", source=None) -> bool:
	"""Resolve the open alert with ``key``. Returns True when an alert was resolved."""
	work = partial(_resolve_alert, key, notes, source)
	return _execute(work, key, "Hostel360: could not resolve ops alert", default=False)


def acknowledge_alert(name: str, user: str | None = None) -> None:
	"""Mark an alert as being worked on; stops the hourly reminders for critical alerts."""
	user = user or frappe.session.user
	frappe.db.get_value(ALERT_DOCTYPE, name, "name", for_update=True)
	transaction.mark_alert_rows_written_inline()
	alert = frappe.get_doc(ALERT_DOCTYPE, name)
	if alert.status == STATUS_ACKNOWLEDGED:
		return
	if alert.status != STATUS_OPEN:
		frappe.throw(_("Only open alerts can be acknowledged. {0} is {1}.").format(name, _(alert.status)))
	alert.status = STATUS_ACKNOWLEDGED
	alert.acknowledged_by = user
	alert.acknowledged_on = now_datetime()
	alert.flags.ignore_permissions = True
	alert.save()
	if notification.notifications_enabled():
		notification.dispatch(alert.name, "acknowledged", [])


def resolve_alert_by_name(name: str, notes: str = "", user: str | None = None) -> None:
	"""Resolve a specific alert from the desk or the ops API."""
	frappe.db.get_value(ALERT_DOCTYPE, name, "name", for_update=True)
	transaction.mark_alert_rows_written_inline()
	alert = frappe.get_doc(ALERT_DOCTYPE, name)
	if alert.status == STATUS_RESOLVED:
		return
	plan = _close(alert, notes, via=None, user=user or frappe.session.user)
	# A desk action: the recovery notice goes out once this request has committed.
	_dispatch_all(_deliveries(alert.name, plan), committed=False)


def suppress_alert(name: str, notes: str = "", user: str | None = None) -> None:
	"""Silence a noisy alert: occurrences keep counting, nobody is notified, until resolved."""
	frappe.db.get_value(ALERT_DOCTYPE, name, "name", for_update=True)
	transaction.mark_alert_rows_written_inline()
	alert = frappe.get_doc(ALERT_DOCTYPE, name)
	if alert.status not in ACTIVE_STATUSES:
		frappe.throw(_("Only open or acknowledged alerts can be suppressed."))
	alert.status = STATUS_SUPPRESSED
	if notes:
		alert.resolution_notes = notes
	alert.flags.ignore_permissions = True
	alert.save()
	alert.add_comment("Info", _("Suppressed by {0}").format(user or frappe.session.user))


# Internals --------------------------------------------------------------


def normalise_key(key) -> str:
	key = " ".join(str(key or "").split())
	if not key:
		raise ValueError("alert key is required")
	if len(key) > MAX_KEY_LENGTH:
		digest = hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12]
		key = f"{key[: MAX_KEY_LENGTH - 13]}~{digest}"
	return key


def _clean(severity, category, source) -> tuple[str, str, str]:
	severity = severity if severity in SEVERITIES else WARNING
	category = category if category in CATEGORIES else "Other"
	source = source if source in SOURCES else "App"
	return severity, category, source


def _details_json(details) -> str | None:
	if details in (None, "", {}):
		return None
	text = details if isinstance(details, str) else json.dumps(details, indent=1, default=str, sort_keys=True)
	if len(text) > MAX_DETAILS_LENGTH:
		text = text[:MAX_DETAILS_LENGTH] + "\n... (truncated)"
	return text


def _execute(work, key, failure_title: str, default):
	"""Run ``work`` in an independent transaction, falling back to the caller's own.

	``work`` returns ``(result, deliveries)``; deliveries are dispatched only once the alert
	row has been committed (or, inline, once the caller commits).
	"""
	messages = _message_count()
	if transaction.use_independent_transactions():
		try:
			with transaction.independent() as db:
				try:
					return _run_independent(work, key, db)
				except Exception:
					_forget_messages(messages)
					db.rollback()
					_log_failure(failure_title, key)
					db.commit()
					return default
		except transaction.IndependentConnectionUnavailable:
			# No second connection to be had (connection limit, for example): writing the
			# alert in the caller's transaction is still better than losing it.
			pass
		except Exception:
			_forget_messages(messages)
			_log_failure(failure_title, key)
			return default

	try:
		return _run_inline(work)
	except frappe.QueryDeadlockError:
		# InnoDB has rolled back the caller's whole transaction. Returning normally would let
		# the caller carry on and commit only the part of its work that comes after this call.
		_forget_messages(messages)
		_log_failure(failure_title, key, defer=True)
		raise
	except Exception:
		_forget_messages(messages)
		_log_failure(failure_title, key)
		return default


def _run_independent(work, key, db):
	lock = transaction.lock_name(normalise_key(key), db)
	for attempt in range(1, MAX_ATTEMPTS + 1):
		# The named lock is taken before anything is read, so the transaction's snapshot is
		# taken after the previous writer of this key has committed.
		if not transaction.acquire_named_lock(lock):
			raise AlertNotRecorded(f"timed out waiting for the lock on {key}")
		try:
			result, deliveries = work()
			db.commit()
		except frappe.QueryDeadlockError:
			db.rollback()
			if attempt == MAX_ATTEMPTS:
				raise AlertNotRecorded(f"gave up after {attempt} deadlocks") from None
			time.sleep(random.uniform(0.05, 0.2) * attempt)
			continue
		except Exception:
			db.rollback()
			raise
		finally:
			transaction.release_named_lock(lock)
		break

	_dispatch_all(deliveries, committed=True)
	# Direct deliveries leave a comment on the alert (and e-mail queue rows) behind.
	db.commit()
	return result


def _run_inline(work):
	frappe.db.savepoint(SAVEPOINT)
	try:
		result, deliveries = work()
	except frappe.QueryDeadlockError:
		# The server has already discarded the transaction, savepoint included.
		raise
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		raise
	transaction.mark_alert_rows_written_inline()
	_dispatch_all(deliveries, committed=False)
	return result


def _deliveries(name: str, plan) -> list[tuple]:
	return [(name, plan.event, plan.levels)] if plan else []


def _dispatch_all(deliveries: list[tuple], committed: bool) -> None:
	for name, event, levels in deliveries:
		try:
			notification.dispatch(name, event, levels, committed=committed)
		except Exception:
			_log_failure("Hostel360: could not send ops alert notification", name)


def _raise_alert(key, title, severity, category, source, description, details, is_outage) -> tuple:
	key = normalise_key(key)
	severity, category, source = _clean(severity, category, source)
	title = (" ".join(str(title or key).split()))[:MAX_TITLE_LENGTH]
	description = str(description or "")[:MAX_DESCRIPTION_LENGTH]
	details_text = _details_json(details)
	is_outage = 1 if is_outage else 0
	now = now_datetime()
	args = (key, title, severity, category, source, description, details_text, is_outage, now)

	frappe.db.savepoint(RETRY_SAVEPOINT)
	messages = _message_count()
	try:
		# A plain read: a locking read of a key that does not exist yet takes a gap lock,
		# and two such gap locks followed by two inserts are a guaranteed deadlock.
		name, plan = _record(*args, locking=False)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Someone opened the same key between our lookup and our insert (only possible for
		# alerts written inline, or created by hand on the desk). The locking read sees
		# their committed row. The duplicate-key message is not meant for the caller.
		_forget_messages(messages)
		frappe.db.rollback(save_point=RETRY_SAVEPOINT)
		name, plan = _record(*args, locking=True)
	return name, _deliveries(name, plan)


def _record(key, title, severity, category, source, description, details_text, is_outage, now, locking=False):
	"""Insert or update the alert row; returns (name, notification plan or None)."""
	settings = get_settings()
	notify = notification.notifications_enabled(settings)

	existing = frappe.db.get_value(ALERT_DOCTYPE, {"open_key": key}, "name", for_update=locking)
	if existing:
		return existing, _add_occurrence(
			existing, settings, notify, title, severity, description, details_text, is_outage, now
		)

	recent = _recently_resolved(key, now)
	if recent:
		return recent, _reopen(recent, settings, notify, title, severity, description, details_text, is_outage, now)

	alert = frappe.get_doc(
		{
			"doctype": ALERT_DOCTYPE,
			"alert_key": key,
			"open_key": key,
			"title": title,
			"severity": severity,
			"category": category,
			"source": source,
			"status": STATUS_OPEN,
			"description": description,
			"details": details_text,
			"is_outage": is_outage,
			"first_seen": now,
			"last_seen": now,
			"occurrence_count": 1,
		}
	)
	alert.flags.raised_by_service = True
	alert.insert(ignore_permissions=True)

	if not notify:
		return alert.name, None
	state = notification.get_alert_state(alert.name)
	plan = notification.plan_notification(state, settings, now)
	if plan:
		notification.record_plan(alert.name, state, plan, now)
	return alert.name, plan


def _add_occurrence(name, settings, notify, title, severity, description, details_text, is_outage, now):
	# A locking read sees the latest committed row, so concurrent occurrences all count.
	state = notification.get_alert_state(name, for_update=True)
	values = {
		"last_seen": now,
		"occurrence_count": cint(state.occurrence_count) + 1,
	}
	# The latest wording wins ("Disk 91% used" becomes "Disk 95% used").
	if title and title != state.title:
		values["title"] = title
	if description:
		values["description"] = description
	if details_text:
		values["details"] = details_text

	escalated = severity_rank(severity) > severity_rank(state.severity)
	if escalated:
		values["severity"] = severity
	became_outage = is_outage and not cint(state.is_outage)
	if became_outage:
		values["is_outage"] = 1

	frappe.db.set_value(ALERT_DOCTYPE, name, values, update_modified=False)
	state.update(values)

	if not notify or state.status == STATUS_SUPPRESSED or not (escalated or became_outage):
		return None

	plan = notification.plan_notification(state, settings, now)
	if escalated and (not plan or plan.event in ("reminder", "reopened")):
		notified = notification.parse_levels(state.notified_levels)
		plan = notification.Plan("severity", [max(notified)] if notified else [])
	elif escalated and plan.event == "escalated":
		plan.event = "severity"
	if plan:
		notification.record_plan(name, state, plan, now)
	return plan


def _recently_resolved(key: str, now) -> str | None:
	from frappe.utils import add_to_date

	cutoff = add_to_date(now, minutes=-REOPEN_WINDOW_MINUTES)
	rows = frappe.get_all(
		ALERT_DOCTYPE,
		filters={"alert_key": key, "status": STATUS_RESOLVED, "resolved_on": (">=", cutoff)},
		fields=["name"],
		order_by="resolved_on desc",
		limit=1,
	)
	return rows[0].name if rows else None


def _reopen(name, settings, notify, title, severity, description, details_text, is_outage, now):
	state = notification.get_alert_state(name, for_update=True)
	reopened = cint(frappe.db.get_value(ALERT_DOCTYPE, name, "reopened_count", for_update=True))
	values = {
		"status": STATUS_OPEN,
		"open_key": state.alert_key,
		"last_seen": now,
		"occurrence_count": cint(state.occurrence_count) + 1,
		"reopened_count": reopened + 1,
		"resolved_on": None,
		"resolved_by": None,
		"resolved_via": None,
		"duration_minutes": 0,
		"acknowledged_by": None,
		"acknowledged_on": None,
	}
	if title:
		values["title"] = title
	if severity_rank(severity) > severity_rank(state.severity):
		values["severity"] = severity
	if is_outage:
		values["is_outage"] = 1
	if description:
		values["description"] = description
	if details_text:
		values["details"] = details_text
	frappe.db.set_value(ALERT_DOCTYPE, name, values, update_modified=False)
	state.update(values)
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": ALERT_DOCTYPE,
			"reference_name": name,
			"content": _("Re-opened: the problem came back within {0} minutes of being resolved.").format(
				REOPEN_WINDOW_MINUTES
			),
		}
	).insert(ignore_permissions=True)

	if not notify:
		return None
	# While the alert is flapping the reopen notice is held back; the escalation job sends
	# it if the alert is still open once the window has passed.
	plan = notification.plan_notification(state, settings, now)
	if plan:
		notification.record_plan(name, state, plan, now)
	return plan


def _resolve_alert(key, notes, source) -> tuple:
	key = normalise_key(key)
	# Look the key up with a plain read and lock only the row found: a locking read of a
	# missing key would take a gap lock that blocks unrelated alerts being opened.
	name = frappe.db.get_value(ALERT_DOCTYPE, {"open_key": key}, "name")
	if not name:
		return False, []
	if frappe.db.get_value(ALERT_DOCTYPE, name, "open_key", for_update=True) != key:
		# Resolved by someone else in the meantime.
		return False, []
	alert = frappe.get_doc(ALERT_DOCTYPE, name)
	plan = _close(alert, notes, via=source if source in SOURCES else None, user=None)
	return True, _deliveries(alert.name, plan)


def _close(alert, notes, via, user):
	"""Resolve ``alert``; returns the recovery notification plan (or None) for the caller to send."""
	now = now_datetime()
	alert.status = STATUS_RESOLVED
	alert.resolved_on = now
	alert.resolved_via = via
	alert.resolved_by = user if user and user != "Guest" else None
	if notes:
		alert.resolution_notes = str(notes)[:2000]
	alert.flags.ignore_permissions = True
	alert.save()

	settings = get_settings()
	if not (
		alert.last_notified_on
		and alert.last_notified_event != "resolved"
		and notification.notifications_enabled(settings)
	):
		return None
	state = notification.get_alert_state(alert.name)
	plan = notification.Plan("resolved", sorted(notification.parse_levels(state.notified_levels)))
	frappe.db.set_value(
		ALERT_DOCTYPE,
		alert.name,
		{"last_notified_on": now, "last_notified_event": "resolved"},
		update_modified=False,
	)
	return plan


def _message_count() -> int:
	return len(getattr(frappe.local, "message_log", None) or [])


def _forget_messages(count: int) -> None:
	"""Drop messages queued for the user by a failed alerting step."""
	log = getattr(frappe.local, "message_log", None)
	if log and len(log) > count:
		del log[count:]


def _log_failure(title: str, key, defer: bool = False) -> None:
	try:
		frappe.log_error(title=title, message=f"Alert key: {key}\n\n{frappe.get_traceback()}", defer_insert=defer)
	except Exception:
		# Logging must never be the thing that breaks the caller.
		frappe.logger("hostel360").exception(title)


def open_alert_counts() -> dict:
	rows = frappe.get_all(
		ALERT_DOCTYPE,
		filters={"status": ("in", list(ACTIVE_STATUSES))},
		fields=["severity", {"COUNT": "*", "as": "count"}],
		group_by="severity",
	)
	counts = dict.fromkeys(SEVERITIES, 0)
	for row in rows:
		counts[row.severity] = cint(row.count)
	return counts
