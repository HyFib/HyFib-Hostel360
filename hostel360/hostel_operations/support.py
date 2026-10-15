"""Support tickets raised by institution staff to the HyFib helpdesk, and their SLAs.

Contracted SLA (tender, bid document 11): first response within 1 hour and resolution
within 4 hours of a ticket being raised. Time spent "Waiting on Institution" does not count
against the resolution SLA. Every ticket opens an Ops Alert (category SLA, source Support)
so the helpdesk is paged through the same channels and escalation matrix as technical
alerts; outage tickets page the Director immediately.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime, strip_html

from hostel360.hostel_operations.constants import CRITICAL, INFO, TICKET_DOCTYPE, WARNING
from hostel360.hostel_operations.settings import get_settings, threshold
from hostel360.setup.roles import OPERATIONS_ROLE

STATUS_OPEN = "Open"
STATUS_WAITING = "Waiting on Institution"
CLOSED_STATUSES = ("Resolved", "Closed")
PRIORITY_SEVERITY = {"Low": INFO, "Medium": INFO, "High": WARNING, "Critical": CRITICAL}
BREACH_SEVERITY = {"Low": WARNING, "Medium": WARNING, "High": CRITICAL, "Critical": CRITICAL}
REOPEN_DAYS = 7


def alert_key(ticket_name: str) -> str:
	return f"support.ticket.{ticket_name}"


def is_operations_user(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return OPERATIONS_ROLE in frappe.get_roles(user)


# Controller hooks (called from SupportTicket) ----------------------------


def before_insert(ticket) -> None:
	now = now_datetime()
	settings = get_settings()
	ticket.raised_by = ticket.raised_by or frappe.session.user
	ticket.status = ticket.status or STATUS_OPEN
	created = get_datetime(ticket.creation) if ticket.get("creation") else now
	ticket.response_due_by = add_to_date(created, hours=threshold(settings, "ticket_response_hours"))
	ticket.resolution_due_by = add_to_date(created, hours=threshold(settings, "ticket_resolution_hours"))


def validate(ticket) -> None:
	from hostel360.utils.validation import normalize_mobile

	if ticket.contact_phone:
		ticket.contact_phone = normalize_mobile(ticket.contact_phone)

	previous = ticket.get_doc_before_save()
	old_status = previous.status if previous else None
	now = now_datetime()

	if ticket.status != old_status:
		_track_status_change(ticket, old_status, now)

	if ticket.status in CLOSED_STATUSES and not strip_html(ticket.resolution_details or "").strip():
		frappe.throw(_("Please describe the resolution before marking the ticket {0}.").format(_(ticket.status)))


def _track_status_change(ticket, old_status, now) -> None:
	# Moving a ticket out of Open (acknowledging it, starting work, asking the institution a
	# question) is the helpdesk's first response.
	if old_status == STATUS_OPEN and ticket.status != STATUS_OPEN and not ticket.first_response_on:
		ticket.first_response_on = now

	# Pause the resolution clock while HyFib waits for the institution.
	if ticket.status == STATUS_WAITING:
		ticket.waiting_since = now
	elif old_status == STATUS_WAITING and ticket.waiting_since:
		paused = now - get_datetime(ticket.waiting_since)
		if ticket.resolution_due_by:
			ticket.resolution_due_by = get_datetime(ticket.resolution_due_by) + paused
		ticket.waiting_since = None

	if ticket.status in CLOSED_STATUSES:
		ticket.resolved_on = ticket.resolved_on or now
	elif old_status in CLOSED_STATUSES:
		ticket.resolved_on = None


def after_insert(ticket) -> None:
	from hostel360.hostel_operations.alerts import raise_alert

	name = raise_alert(
		alert_key(ticket.name),
		_("Support ticket {0}: {1}").format(ticket.name, ticket.subject),
		CRITICAL if ticket.category == "Outage" else PRIORITY_SEVERITY.get(ticket.priority, INFO),
		"SLA",
		source="Support",
		description=_alert_description(ticket),
		details={
			"ticket": ticket.name,
			"priority": ticket.priority,
			"category": ticket.category,
			"raised_by": ticket.raised_by,
			"response_due_by": str(ticket.response_due_by),
			"resolution_due_by": str(ticket.resolution_due_by),
		},
		is_outage=ticket.category == "Outage",
	)
	if name:
		ticket.db_set("ops_alert", name, update_modified=False)


def on_update(ticket) -> None:
	previous = ticket.get_doc_before_save()
	if not previous or previous.status == ticket.status:
		return
	_sync_alert(ticket, previous.status)
	_notify_raiser(ticket, previous.status)


def _sync_alert(ticket, old_status) -> None:
	from hostel360.hostel_operations import alerts

	key = alert_key(ticket.name)
	if ticket.status in CLOSED_STATUSES:
		alerts.resolve_alert(
			key,
			notes=_("Ticket {0}: {1}").format(_(ticket.status), strip_html(ticket.resolution_details or "")[:500]),
			source="Support",
		)
		return
	if old_status in CLOSED_STATUSES:
		# Re-opened by the institution: page the helpdesk again.
		alerts.raise_alert(
			key,
			_("Support ticket {0} re-opened: {1}").format(ticket.name, ticket.subject),
			PRIORITY_SEVERITY.get(ticket.priority, INFO),
			"SLA",
			source="Support",
			description=_alert_description(ticket),
		)
		return
	if ticket.status != STATUS_OPEN and ticket.ops_alert:
		status = frappe.db.get_value("Ops Alert", ticket.ops_alert, "status")
		if status == "Open":
			try:
				alerts.acknowledge_alert(ticket.ops_alert, ticket.assigned_to or frappe.session.user)
			except frappe.ValidationError:
				pass


def _notify_raiser(ticket, old_status) -> None:
	if not ticket.raised_by or ticket.raised_by == frappe.session.user:
		return
	try:
		from hostel360.hostel_communication.dispatch import notify, recipients_for_users
	except ImportError:
		return
	try:
		notify(
			"support_ticket_update",
			recipients_for_users([ticket.raised_by]),
			{
				"ticket": ticket.name,
				"subject": ticket.subject,
				"status": ticket.status,
				"previous_status": old_status,
				"assigned_to": ticket.assigned_to,
				"resolution": strip_html(ticket.resolution_details or "")[:500],
			},
			reference_doctype=TICKET_DOCTYPE,
			reference_name=ticket.name,
		)
	except Exception:
		frappe.log_error(title="Hostel360: support ticket update notification failed", message=frappe.get_traceback())


def _alert_description(ticket) -> str:
	text = strip_html(ticket.description or "").strip()
	if len(text) > 1500:
		text = text[:1497] + "..."
	lines = [
		_("Priority: {0}. Category: {1}.").format(_(ticket.priority), _(ticket.category)),
		_("Raised by {0}{1}.").format(
			ticket.raised_by_name or ticket.raised_by, f" ({ticket.contact_phone})" if ticket.contact_phone else ""
		),
		_("First response due by {0}; resolution due by {1}.").format(
			_fmt(ticket.response_due_by),
			_fmt(ticket.resolution_due_by),
		),
	]
	if text:
		lines.extend(["", text])
	return "\n".join(lines)


def close_ticket(ticket, user: str | None = None) -> None:
	"""The raiser confirms a resolved ticket is done."""
	user = user or frappe.session.user
	if ticket.status != "Resolved":
		frappe.throw(_("Only resolved tickets can be closed."))
	if user != ticket.raised_by and not is_operations_user(user):
		frappe.throw(_("Only the person who raised the ticket can close it."), frappe.PermissionError)
	ticket.status = "Closed"
	ticket.flags.ignore_permissions = True
	ticket.save()


def reopen_ticket(ticket, reason: str, user: str | None = None) -> None:
	"""The raiser reports that a resolved problem is back, within a week of resolution."""
	user = user or frappe.session.user
	if ticket.status not in CLOSED_STATUSES:
		frappe.throw(_("Only resolved or closed tickets can be re-opened."))
	if user != ticket.raised_by and not is_operations_user(user):
		frappe.throw(_("Only the person who raised the ticket can re-open it."), frappe.PermissionError)
	reason = strip_html(reason or "").strip()
	if not reason:
		frappe.throw(_("Please say why the ticket is being re-opened."))
	if ticket.resolved_on and (now_datetime() - get_datetime(ticket.resolved_on)).days > REOPEN_DAYS:
		frappe.throw(
			_("This ticket was resolved more than {0} days ago. Please raise a new ticket.").format(REOPEN_DAYS)
		)
	ticket.status = STATUS_OPEN
	ticket.resolution_due_by = add_to_date(
		now_datetime(), hours=threshold(get_settings(), "ticket_resolution_hours")
	)
	ticket.flags.ignore_permissions = True
	ticket.save()
	ticket.add_comment("Comment", _("Re-opened: {0}").format(frappe.utils.escape_html(reason)))


# Comments -----------------------------------------------------------------


def on_comment(doc, method=None) -> None:
	"""Comment hook: a HyFib comment on a ticket counts as the first response."""
	if doc.reference_doctype != TICKET_DOCTYPE or doc.comment_type != "Comment":
		return
	if not is_operations_user(doc.owner):
		return
	ticket = frappe.db.get_value(
		TICKET_DOCTYPE, doc.reference_name, ["name", "first_response_on", "raised_by", "subject", "status"], as_dict=True
	)
	if not ticket:
		return
	if not ticket.first_response_on:
		frappe.db.set_value(
			TICKET_DOCTYPE, ticket.name, "first_response_on", get_datetime(doc.creation), update_modified=False
		)
	if ticket.raised_by and ticket.raised_by != doc.owner:
		try:
			from hostel360.hostel_communication.dispatch import notify, recipients_for_users

			notify(
				"support_ticket_update",
				recipients_for_users([ticket.raised_by]),
				{
					"ticket": ticket.name,
					"subject": ticket.subject,
					"status": ticket.status,
					"comment": strip_html(doc.content or "")[:500],
				},
				reference_doctype=TICKET_DOCTYPE,
				reference_name=ticket.name,
			)
		except ImportError:
			pass
		except Exception:
			frappe.log_error(title="Hostel360: support ticket comment notification failed", message=frappe.get_traceback())


def _backfill_first_responses(names: list[str]) -> None:
	"""Pick up HyFib comments on tickets that still have no first response recorded.

	Covers comments added while the comment hook was not installed or failed.
	"""
	if not names:
		return
	comments = frappe.get_all(
		"Comment",
		filters={"reference_doctype": TICKET_DOCTYPE, "reference_name": ("in", names), "comment_type": "Comment"},
		fields=["reference_name", "owner", "creation"],
		order_by="creation asc",
	)
	done = set()
	for comment in comments:
		if comment.reference_name in done or not is_operations_user(comment.owner):
			continue
		frappe.db.set_value(
			TICKET_DOCTYPE, comment.reference_name, "first_response_on", comment.creation, update_modified=False
		)
		done.add(comment.reference_name)


# SLA monitoring -----------------------------------------------------------


def check_ticket_slas(now=None) -> dict:
	"""Every five minutes: flag breached SLAs, alert on them, keep escalation levels current."""
	from hostel360.hostel_operations import alerts, notification

	now = get_datetime(now) if now else now_datetime()
	tickets = frappe.get_all(
		TICKET_DOCTYPE,
		filters={"status": ("not in", list(CLOSED_STATUSES))},
		fields=[
			"name",
			"subject",
			"priority",
			"category",
			"status",
			"creation",
			"response_due_by",
			"resolution_due_by",
			"first_response_on",
			"response_sla_breached",
			"resolution_sla_breached",
			"escalation_level",
			"ops_alert",
			"waiting_since",
		],
		limit=1000,
	)
	_backfill_first_responses([t.name for t in tickets if not t.first_response_on])

	summary = {"response_breaches": 0, "resolution_breaches": 0, "escalated": 0}
	for ticket in tickets:
		try:
			first_response = frappe.db.get_value(TICKET_DOCTYPE, ticket.name, "first_response_on")
			breaches = []
			values = {}
			if not cint(ticket.response_sla_breached) and not first_response and ticket.response_due_by:
				if now > get_datetime(ticket.response_due_by):
					values["response_sla_breached"] = 1
					breaches.append(_("first response was due by {0}").format(_fmt(ticket.response_due_by)))
					summary["response_breaches"] += 1
			if (
				not cint(ticket.resolution_sla_breached)
				and ticket.status != STATUS_WAITING
				and ticket.resolution_due_by
				and now > get_datetime(ticket.resolution_due_by)
			):
				values["resolution_sla_breached"] = 1
				breaches.append(_("resolution was due by {0}").format(_fmt(ticket.resolution_due_by)))
				summary["resolution_breaches"] += 1

			level = _ticket_level(ticket, now)
			if level > cint(ticket.escalation_level):
				values["escalation_level"] = level
				summary["escalated"] += 1

			if values:
				frappe.db.set_value(TICKET_DOCTYPE, ticket.name, values, update_modified=False)

			if breaches:
				description = _("SLA breached for support ticket {0}: {1}.").format(ticket.name, "; ".join(breaches))
				notified_before = frappe.db.get_value(
					"Ops Alert", {"open_key": alert_key(ticket.name)}, "last_notified_on"
				)
				name = alerts.raise_alert(
					alert_key(ticket.name),
					_("SLA breached - support ticket {0}: {1}").format(ticket.name, ticket.subject),
					BREACH_SEVERITY.get(ticket.priority, WARNING),
					"SLA",
					source="Support",
					description=description,
					is_outage=ticket.category == "Outage",
				)
				# raise_alert commits on its own connection. Commit here too so the reads and
				# writes below see that row instead of this transaction's older snapshot.
				_commit()
				if name and frappe.db.get_value("Ops Alert", name, "last_notified_on") == notified_before:
					# The alert was already at this severity, so raising it again told nobody.
					_send_breach_notice(name, now, notification)
				frappe.get_doc(TICKET_DOCTYPE, ticket.name).add_comment("Info", description)
			_commit()
		except Exception:
			_rollback()
			frappe.log_error(
				title="Hostel360: SLA check failed",
				message=frappe.get_traceback(),
				reference_doctype=TICKET_DOCTYPE,
				reference_name=ticket.name,
			)
	return summary


def _send_breach_notice(alert_name: str, now, notification) -> None:
	if not notification.notifications_enabled():
		return
	state = notification.get_alert_state(alert_name)
	if not state or state.status == "Suppressed":
		return
	notified = notification.parse_levels(state.notified_levels)
	plan = notification.Plan("sla_breach", [max(notified)] if notified else [])
	notification.record_plan(alert_name, state, plan, now)
	notification.dispatch(alert_name, plan.event, plan.levels)


def _ticket_level(ticket, now) -> int:
	"""Escalation level by the ticket's age, following the alert escalation matrix."""
	from hostel360.hostel_operations.settings import get_levels

	age = (now - get_datetime(ticket.creation)).total_seconds() / 60
	level = 0
	for row in get_levels(get_settings()):
		if age >= cint(row.after_minutes) or (ticket.category == "Outage" and cint(row.outage_immediate)):
			level = max(level, cint(row.level))
	return level


def sla_compliance(start, end) -> dict:
	"""Tickets raised in [start, end): counts and percentage meeting both SLAs."""
	tickets = frappe.get_all(
		TICKET_DOCTYPE,
		filters={"creation": ("between", [start, end])},
		fields=["name", "status", "response_sla_breached", "resolution_sla_breached", "resolved_on"],
	)
	resolved = frappe.db.count(TICKET_DOCTYPE, {"resolved_on": ("between", [start, end])})
	met = sum(1 for t in tickets if not cint(t.response_sla_breached) and not cint(t.resolution_sla_breached))
	return {
		"raised": len(tickets),
		"resolved": resolved,
		"compliance_percent": flt(met * 100.0 / len(tickets), 2) if tickets else 100.0,
	}


def _fmt(value) -> str:
	return frappe.utils.format_datetime(value, "dd-MM-yyyy HH:mm") if value else ""


def _commit() -> None:
	if not frappe.flags.in_test:
		frappe.db.commit()  # nosemgrep


def _rollback() -> None:
	if not frappe.flags.in_test:
		frappe.db.rollback()


# Desk helpers -------------------------------------------------------------


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def operations_user_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link search for ``assigned_to``: enabled users holding the HyFib Operations role."""
	has_role = frappe.qb.DocType("Has Role")
	user = frappe.qb.DocType("User")
	pattern = f"%{txt}%"
	return (
		frappe.qb.from_(user)
		.join(has_role)
		.on((has_role.parent == user.name) & (has_role.parenttype == "User"))
		.select(user.name, user.full_name)
		.where(
			(has_role.role == OPERATIONS_ROLE)
			& (user.enabled == 1)
			& ((user.name.like(pattern)) | (user.full_name.like(pattern)))
		)
		.distinct()
		.orderby(user.full_name)
		.limit(cint(page_len) or 20)
		.offset(cint(start))
	).run()
