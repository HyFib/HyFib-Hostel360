"""Ops alert lifecycle: raise, de-duplicate, acknowledge, suppress and resolve.

Any part of Hostel360 (and the host agent, OCI Monitoring and the uptime probe through the
ops API) reports a problem with ``raise_alert``. A stable ``key`` identifies the problem
("health.redis.cache", "backup.daily.failed"); while an alert with that key is open, further
occurrences only bump its counters, so a check failing every five minutes produces one
alert, not hundreds.

``raise_alert`` and ``resolve_alert`` never raise. They are called from payment webhooks,
scheduler jobs and error handlers, and an alerting fault must not turn into a second
failure for the caller; problems are written to the Error Log instead.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from hostel360.hostel_operations import notification
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
	messages = _message_count()
	try:
		return _raise_alert(key, title, severity, category, source, description, details, is_outage)
	except Exception:
		_forget_messages(messages)
		_log_failure("Hostel360: could not raise ops alert", key)
		return ""


def resolve_alert(key, notes="", source=None) -> bool:
	"""Resolve the open alert with ``key``. Returns True when an alert was resolved."""
	messages = _message_count()
	try:
		return _resolve_alert(key, notes, source)
	except Exception:
		_forget_messages(messages)
		_log_failure("Hostel360: could not resolve ops alert", key)
		return False


def acknowledge_alert(name: str, user: str | None = None) -> None:
	"""Mark an alert as being worked on; stops the hourly reminders for critical alerts."""
	user = user or frappe.session.user
	frappe.db.get_value(ALERT_DOCTYPE, name, "name", for_update=True)
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
	alert = frappe.get_doc(ALERT_DOCTYPE, name)
	if alert.status == STATUS_RESOLVED:
		return
	_close(alert, notes, via=None, user=user or frappe.session.user)


def suppress_alert(name: str, notes: str = "", user: str | None = None) -> None:
	"""Silence a noisy alert: occurrences keep counting, nobody is notified, until resolved."""
	frappe.db.get_value(ALERT_DOCTYPE, name, "name", for_update=True)
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


def _raise_alert(key, title, severity, category, source, description, details, is_outage) -> str:
	key = normalise_key(key)
	severity, category, source = _clean(severity, category, source)
	title = (" ".join(str(title or key).split()))[:MAX_TITLE_LENGTH]
	description = str(description or "")[:MAX_DESCRIPTION_LENGTH]
	details_text = _details_json(details)
	is_outage = 1 if is_outage else 0
	now = now_datetime()

	frappe.db.savepoint(SAVEPOINT)
	messages = _message_count()
	try:
		try:
			name, plan = _record(key, title, severity, category, source, description, details_text, is_outage, now)
		except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
			# Another worker opened the same key between our lookup and insert; the
			# second attempt finds and updates that alert. The duplicate-key message is
			# not meant for whoever triggered the alert.
			_forget_messages(messages)
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.db.savepoint(SAVEPOINT)
			name, plan = _record(key, title, severity, category, source, description, details_text, is_outage, now)
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		raise

	if plan:
		try:
			notification.dispatch(name, plan.event, plan.levels)
		except Exception:
			_log_failure("Hostel360: could not send ops alert notification", key)
	return name


def _record(key, title, severity, category, source, description, details_text, is_outage, now):
	"""Insert or update the alert row; returns (name, notification plan or None)."""
	settings = get_settings()
	notify = notification.notifications_enabled(settings)

	existing = frappe.db.get_value(ALERT_DOCTYPE, {"open_key": key}, "name", for_update=True)
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


def _resolve_alert(key, notes, source) -> bool:
	key = normalise_key(key)
	frappe.db.savepoint(SAVEPOINT)
	try:
		name = frappe.db.get_value(ALERT_DOCTYPE, {"open_key": key}, "name", for_update=True)
		if not name:
			return False
		alert = frappe.get_doc(ALERT_DOCTYPE, name)
		_close(alert, notes, via=source if source in SOURCES else None, user=None)
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		raise
	return True


def _close(alert, notes, via, user) -> None:
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
	if alert.last_notified_on and alert.last_notified_event != "resolved" and notification.notifications_enabled(settings):
		state = notification.get_alert_state(alert.name)
		plan = notification.Plan("resolved", sorted(notification.parse_levels(state.notified_levels)))
		frappe.db.set_value(
			ALERT_DOCTYPE,
			alert.name,
			{"last_notified_on": now, "last_notified_event": "resolved"},
			update_modified=False,
		)
		try:
			notification.dispatch(alert.name, plan.event, plan.levels)
		except Exception:
			_log_failure("Hostel360: could not send recovery notification", alert.alert_key)


def _message_count() -> int:
	return len(getattr(frappe.local, "message_log", None) or [])


def _forget_messages(count: int) -> None:
	"""Drop messages queued for the user by a failed alerting step."""
	log = getattr(frappe.local, "message_log", None)
	if log and len(log) > count:
		del log[count:]


def _log_failure(title: str, key) -> None:
	try:
		frappe.log_error(title=title, message=f"Alert key: {key}\n\n{frappe.get_traceback()}")
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
