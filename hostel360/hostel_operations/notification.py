"""Who is told about an alert, when, and through which channels.

The escalation matrix in Ops Alert Settings decides *who*: level 1 (the HyFib helpdesk)
hears about everything it accepts, later levels join once an alert has been unresolved for
their ``after_minutes`` or immediately for an outage when ``outage_immediate`` is set.
Each alert remembers which levels have been told (``notified_levels``), so the minute-by-
minute escalation job never pages the same level twice for the same step.

The channel table decides *how*: every enabled channel whose minimum severity the alert
meets receives each notification, in addition to the e-mail addresses and mobile numbers
of the levels being notified.
"""

from dataclasses import dataclass, field
from functools import partial

import frappe
from frappe.utils import (
	add_to_date,
	cint,
	format_datetime,
	get_datetime,
	get_url_to_form,
	now_datetime,
	time_diff_in_seconds,
)

from hostel360.hostel_operations import channels
from hostel360.hostel_operations.constants import (
	ALERT_DOCTYPE,
	CERT_IN_REPORTING_HOURS,
	CHANNEL_EMAIL,
	CHANNEL_SMS,
	CHANNEL_WHATSAPP,
	CRITICAL,
	EVENT_LABELS,
	MOBILE_CHANNELS,
	REOPEN_WINDOW_MINUTES,
	STATUS_OPEN,
	STATUS_SUPPRESSED,
	WEBHOOK_CHANNELS,
	severity_rank,
)
from hostel360.hostel_operations.settings import (
	format_levels,
	get_levels,
	get_settings,
	level_accepts,
	parse_emails,
	parse_levels,
	parse_mobiles,
	threshold,
)

ALERT_STATE_FIELDS = (
	"name",
	"alert_key",
	"title",
	"severity",
	"category",
	"source",
	"status",
	"description",
	"is_outage",
	"first_seen",
	"last_seen",
	"occurrence_count",
	"escalation_level",
	"notified_levels",
	"last_notified_on",
	"last_notified_event",
	"acknowledged_by",
	"resolved_on",
)


@dataclass
class Plan:
	event: str
	levels: list[int] = field(default_factory=list)


def minutes_between(start, end) -> float:
	if not start:
		return 0.0
	return time_diff_in_seconds(get_datetime(end), get_datetime(start)) / 60.0


def notifications_enabled(settings=None) -> bool:
	settings = settings or get_settings()
	if frappe.flags.in_install or frappe.flags.in_migrate:
		return False
	return bool(cint(settings.get("enabled")))


# Planning -------------------------------------------------------------------


def plan_notification(alert, settings, now) -> Plan | None:
	"""Decide whether ``alert`` needs a notification at ``now``.

	Order of precedence: the first notice of a new alert; levels that have become due
	(age or outage); a re-opened alert whose notice was held back while it was flapping;
	the hourly reminder for an unacknowledged critical alert.
	"""
	if alert.status == STATUS_SUPPRESSED:
		return None

	notified = parse_levels(alert.notified_levels)
	age = minutes_between(alert.first_seen, now)
	due = []
	for row in get_levels(settings):
		level = cint(row.level)
		if level <= 0 or level in notified or not level_accepts(row, alert.severity):
			continue
		if age >= cint(row.after_minutes) or (cint(alert.is_outage) and cint(row.outage_immediate)):
			due.append(level)

	outage_due = cint(alert.is_outage) and any(
		cint(row.level) in due and cint(row.outage_immediate) for row in get_levels(settings)
	)

	if not alert.last_notified_on:
		return Plan("outage" if cint(alert.is_outage) else "raised", due)
	if due:
		return Plan("outage" if outage_due else "escalated", due)

	current = [max(notified)] if notified else []
	since = minutes_between(alert.last_notified_on, now)
	if alert.last_notified_event == "resolved" and since >= REOPEN_WINDOW_MINUTES:
		return Plan("reopened", current)
	if (
		alert.status == STATUS_OPEN
		and alert.severity == CRITICAL
		and since >= threshold(settings, "critical_renotify_minutes")
	):
		return Plan("reminder", current)
	return None


def record_plan(alert_name: str, alert, plan: Plan, now) -> None:
	"""Write the notification bookkeeping for ``plan`` onto the alert row."""
	notified = parse_levels(alert.notified_levels) | set(plan.levels)
	values = {
		"notified_levels": format_levels(notified),
		"escalation_level": max(notified) if notified else 0,
		"last_notified_on": now,
		"last_notified_event": plan.event,
	}
	frappe.db.set_value(ALERT_DOCTYPE, alert_name, values, update_modified=False)
	alert.update(values)


def get_alert_state(alert_name: str, for_update: bool = False):
	return frappe.db.get_value(
		ALERT_DOCTYPE, alert_name, list(ALERT_STATE_FIELDS), as_dict=True, for_update=for_update
	)


# Dispatch -------------------------------------------------------------------


def dispatch(alert_name: str, event: str, levels: list[int] | None = None, committed: bool = False) -> None:
	"""Deliver now, or through the job queue when called from a web request.

	``committed`` says whether the alert row is already committed (alerts raised through
	``alerts.raise_alert`` are written in a transaction of their own). When it is not, a web
	request's alert is invisible to anyone else until the request commits, and the request
	may still roll back; delivery is then queued after commit. Scheduler jobs, background
	jobs and tests deliver straight away.
	"""
	levels = sorted(set(levels or []))
	if _in_web_request():
		if committed:
			_enqueue_delivery(alert_name, event, levels)
		else:
			frappe.db.after_commit.add(partial(_enqueue_delivery, alert_name, event, levels))
		return
	deliver(alert_name, event, levels)


def _in_web_request() -> bool:
	return (
		bool(getattr(frappe.local, "request", None))
		and not frappe.flags.in_test
		and not getattr(frappe, "job", None)
	)


def alert_queue() -> str:
	"""Queue for alert deliveries: ``hostel360_alert_queue`` in site config, else "short".

	A dedicated queue (with a worker that lists it first) keeps pages independent of bulk
	student notifications; jobs are put at the front of whichever queue is used.
	"""
	return frappe.conf.get("hostel360_alert_queue") or "short"


def _enqueue_delivery(alert_name: str, event: str, levels: list[int]) -> None:
	try:
		frappe.enqueue(
			"hostel360.hostel_operations.notification.deliver_queued",
			queue=alert_queue(),
			# A page must not wait behind thousands of fee or mess-bill notifications.
			at_front=True,
			timeout=300,
			alert_name=alert_name,
			# "event" is a parameter of frappe.enqueue itself and would never reach the job.
			alert_event=event,
			levels=levels,
		)
	except Exception:
		# The job queue itself may be what is failing; tell people directly instead.
		deliver(alert_name, event, levels)


def deliver_queued(alert_name: str, alert_event: str, levels: list[int] | None = None) -> dict:
	"""Background job entry point for ``deliver``."""
	return deliver(alert_name, alert_event, levels)


def deliver(alert_name: str, event: str, levels: list[int] | None = None) -> dict:
	"""Send one notification for an alert to every matching channel and level contact."""
	settings = get_settings()
	alert = frappe.get_doc(ALERT_DOCTYPE, alert_name)
	levels = levels or []
	message = build_message(alert, settings, event, levels)
	targets = resolve_targets(settings, alert, event, levels)
	results = send_to_targets(targets, message, settings)
	_record_delivery(alert, event, levels, results, unreachable_levels(settings, alert, event, levels))
	return {"event": event, "levels": levels, "results": results}


def unreachable_levels(settings, alert, event: str, levels: list[int]) -> list:
	"""Escalation rows among ``levels`` that have neither an e-mail address nor a mobile number."""
	if event == "acknowledged":
		return []
	rows = []
	for row in get_levels(settings):
		if cint(row.level) not in levels or not level_accepts(row, alert.get("severity")):
			continue
		if not parse_emails(row.notify_emails) and not parse_mobiles(row.notify_mobiles):
			rows.append(row)
	return rows


def send_to_targets(targets: list[dict], message: dict, settings) -> list[dict]:
	results = []
	allow_http = settings.get("environment") == "Development"

	emails = [t for t in targets if t["channel_type"] == CHANNEL_EMAIL]
	if emails:
		ok, info = channels.send_email([t["to"] for t in emails], message)
		results.extend(
			{"channel_type": CHANNEL_EMAIL, "label": t["label"], "ok": ok, "info": info} for t in emails
		)

	for target in targets:
		kind = target["channel_type"]
		if kind in MOBILE_CHANNELS:
			ok, info = channels.send_text(kind, target["to"], message)
		elif kind in WEBHOOK_CHANNELS:
			ok, info = channels.post_webhook(
				kind,
				target.get("url"),
				message,
				signing_secret=target.get("signing_secret"),
				allow_http=allow_http,
			)
		else:
			continue
		results.append({"channel_type": kind, "label": target["label"], "ok": ok, "info": info})
	return results


def _record_delivery(alert, event: str, levels: list[int], results: list[dict], unreachable=None) -> None:
	from frappe.utils import escape_html

	unreachable = unreachable or []
	if not results and not unreachable:
		return
	delivered = [r for r in results if r["ok"]]
	failed = [r for r in results if not r["ok"]]
	level_text = ", ".join(f"L{level}" for level in levels) if levels else "channels"
	lines = [
		f"{EVENT_LABELS.get(event, event)} notification ({level_text}): "
		f"{len(delivered)} of {len(results)} delivered."
	]
	lines.extend(f"Not delivered: {r['channel_type']} {r['label']} - {r['info']}" for r in failed)
	# A level without contacts would otherwise look like a successful escalation.
	lines.extend(
		f"Level {cint(row.level)} ({row.label or '-'}) has no e-mail address or mobile number in "
		"Ops Alert Settings, so nobody at that level was told."
		for row in unreachable
	)
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": ALERT_DOCTYPE,
				"reference_name": alert.name,
				"content": "<br>".join(escape_html(line) for line in lines),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Hostel360: could not record alert delivery", message=frappe.get_traceback())
	if failed or unreachable:
		frappe.log_error(
			title="Hostel360: alert notification not delivered",
			message="\n".join(lines),
			reference_doctype=ALERT_DOCTYPE,
			reference_name=alert.name,
		)


# Messages and targets ----------------------------------------------------


def _display(value) -> str:
	if not value:
		return ""
	return format_datetime(get_datetime(value), "dd-MM-yyyy HH:mm")


def _duration(minutes: float) -> str:
	minutes = round(minutes)
	if minutes < 60:
		return f"{minutes} min"
	hours, mins = divmod(minutes, 60)
	if hours < 48:
		return f"{hours} h {mins} min" if mins else f"{hours} h"
	days, hours = divmod(hours, 24)
	return f"{days} d {hours} h"


def build_message(alert, settings, event: str, levels: list[int]) -> dict:
	level_rows = {cint(row.level): row for row in get_levels(settings)}
	top = max(levels) if levels else 0
	escalation_label = ""
	if top and event in ("escalated", "outage", "reminder", "reopened", "severity"):
		row = level_rows.get(top)
		escalation_label = f"Level {top}" + (f" - {row.label}" if row and row.label else "")

	now = now_datetime()
	duration = ""
	if event == "resolved" and alert.get("first_seen"):
		duration = _duration(minutes_between(alert.first_seen, alert.get("resolved_on") or now))

	# CERT-In directions: a reportable incident must reach CERT-In (and, by contract, the
	# institution) within six hours of detection. Say so on every critical security notice.
	reporting_due = ""
	if (
		alert.get("category") == "Security"
		and alert.get("severity") == CRITICAL
		and event not in ("resolved", "acknowledged")
		and alert.get("first_seen")
		# A Security Incident's own deadline alert already states its (earlier) deadline.
		and not str(alert.get("alert_key") or "").startswith("security.incident.")
	):
		reporting_due = _display(
			add_to_date(get_datetime(alert.get("first_seen")), hours=CERT_IN_REPORTING_HOURS)
		)

	name = alert.get("name")
	return {
		"event": event,
		"event_label": EVENT_LABELS.get(event, event.title()),
		"alert": name,
		"alert_key": alert.get("alert_key"),
		"title": alert.get("title") or "",
		"severity": alert.get("severity") or "Warning",
		"category": alert.get("category") or "Other",
		"source": alert.get("source") or "App",
		"status": alert.get("status"),
		"description": (alert.get("description") or "")[:3000],
		"is_outage": cint(alert.get("is_outage")),
		"first_seen": str(alert.get("first_seen") or ""),
		"last_seen": str(alert.get("last_seen") or ""),
		"first_seen_display": _display(alert.get("first_seen")),
		"last_seen_display": _display(alert.get("last_seen")),
		"occurrence_count": cint(alert.get("occurrence_count")) or 1,
		"escalation_level": top or cint(alert.get("escalation_level")),
		"escalation_label": escalation_label,
		"duration_display": duration,
		"reporting_due_display": reporting_due,
		"acknowledged_by": alert.get("acknowledged_by") if event == "acknowledged" else None,
		"instance_label": settings.get("instance_label") or "Hostel360",
		"environment": settings.get("environment") or "Production",
		"url": get_url_to_form(ALERT_DOCTYPE, name) if name else None,
		"sent_at": now.isoformat(),
	}


def resolve_targets(settings, alert, event: str, levels: list[int]) -> list[dict]:
	"""Channel rows plus the contacts of level 1 and ``levels``, de-duplicated."""
	from hostel360.utils.validation import mask_email, mask_mobile

	rank = severity_rank(alert.get("severity"))
	targets: list[dict] = []
	seen: set[tuple[str, str]] = set()

	def add(target: dict) -> None:
		marker = (target["channel_type"], target.get("to") or target.get("url") or "")
		if marker in seen or not marker[1]:
			return
		seen.add(marker)
		targets.append(target)

	for row in settings.get("channels") or []:
		if not cint(row.enabled) or rank < severity_rank(row.min_severity or "Warning"):
			continue
		kind = row.channel_type
		if event == "acknowledged" and kind not in WEBHOOK_CHANNELS:
			continue
		if kind == CHANNEL_EMAIL:
			for email in parse_emails(row.target):
				add({"channel_type": kind, "to": email, "label": mask_email(email)})
		elif kind in MOBILE_CHANNELS:
			for mobile in parse_mobiles(row.target):
				add({"channel_type": kind, "to": mobile, "label": mask_mobile(mobile)})
		elif kind in WEBHOOK_CHANNELS:
			url = row.get_password("secret_url", raise_exception=False) if row.secret_url else None
			signing = (
				row.get_password("signing_secret", raise_exception=False)
				if row.get("signing_secret")
				else None
			)
			add(
				{
					"channel_type": kind,
					"url": url,
					"signing_secret": signing,
					"label": row.target or kind,
				}
			)

	if event == "acknowledged":
		return targets

	level_rows = get_levels(settings)
	wanted = set(levels)
	if level_rows:
		wanted.add(cint(level_rows[0].level))
	for row in level_rows:
		if cint(row.level) not in wanted or not level_accepts(row, alert.get("severity")):
			continue
		for email in parse_emails(row.notify_emails):
			add({"channel_type": CHANNEL_EMAIL, "to": email, "label": mask_email(email)})
		if rank >= severity_rank(row.mobile_min_severity or CRITICAL):
			mobile_channels = {
				"SMS": [CHANNEL_SMS],
				"WhatsApp": [CHANNEL_WHATSAPP],
				"SMS and WhatsApp": [CHANNEL_SMS, CHANNEL_WHATSAPP],
			}.get(row.mobile_channel or "SMS", [CHANNEL_SMS])
			for mobile in parse_mobiles(row.notify_mobiles):
				for kind in mobile_channels:
					add({"channel_type": kind, "to": mobile, "label": mask_mobile(mobile)})
	return targets
