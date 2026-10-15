"""Monthly server performance report for the institution (technical requirement 13).

Produced at 07:00 on the 1st for the previous month, submitted, rendered with the
"Monthly Performance Report" print format and e-mailed as a PDF to the institution
officials in Ops Alert Settings (or, failing that, the hostel office e-mail) with a copy to
the HyFib helpdesk. Generation is idempotent per month: re-running it never creates a second
report and never e-mails a report twice. A report is only marked sent once it has gone to
someone at the institution; until then the health checks retry it and raise an alert as the
6th, the contractual deadline, approaches.

Figures come from other modules (students, payments, messaging) through defensive queries,
so a module that is not installed yet simply reports zero instead of breaking the report.
"""

import re

import frappe
from frappe import _
from frappe.utils import (
	add_months,
	cint,
	flt,
	format_date,
	get_datetime,
	get_first_day,
	get_last_day,
	getdate,
	now_datetime,
)

from hostel360.hostel_operations.constants import (
	ALERT_DOCTYPE,
	BACKUP_DOCTYPE,
	CRITICAL,
	REPORT_DOCTYPE,
	WARNING,
)
from hostel360.hostel_operations.settings import get_settings, level_one_emails, parse_emails
from hostel360.setup.roles import OPERATIONS_ROLE, PORTAL_ROLES

PRINT_FORMAT = "Monthly Performance Report"
UPTIME_TARGET = 99.9
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_INCIDENT_ROWS = 50
BACKUP_TYPES = ("Daily", "Weekly", "Monthly", "Snapshot")
NO_RECIPIENTS_ALERT_KEY = "reports.monthly.no_recipients"
# TR 13: the report reaches the institution by the 6th of the following month.
DELIVERY_DEADLINE_DAY = 6


def previous_month(today=None) -> str:
	first = get_first_day(getdate(today) if today else getdate())
	return add_months(first, -1).strftime("%Y-%m")


def month_bounds(month: str):
	if not MONTH_RE.match(month or ""):
		frappe.throw(_("Month must be in YYYY-MM format, for example 2026-09."))
	from_date = getdate(f"{month}-01")
	to_date = get_last_day(from_date)
	return from_date, to_date, get_datetime(from_date), get_datetime(add_months(from_date, 1))


def generate_monthly_performance_report(month=None, send=True) -> str:
	"""Create, submit and e-mail the report for ``month`` (default: previous month)."""
	month = month or previous_month()
	name = frappe.db.get_value(REPORT_DOCTYPE, {"month": month, "docstatus": 1}, "name")
	if not name:
		frappe.db.savepoint("h360_monthly_report")
		try:
			name = _create_and_submit(month)
		except frappe.DuplicateEntryError:
			# A parallel run created it first.
			frappe.db.rollback(save_point="h360_monthly_report")
			name = frappe.db.get_value(REPORT_DOCTYPE, {"month": month, "docstatus": 1}, "name")
	_commit()

	if send and name and not frappe.db.get_value(REPORT_DOCTYPE, name, "sent_on"):
		send_report(name)
		_commit()
	return name


def _create_and_submit(month: str) -> str:
	draft = frappe.db.get_value(REPORT_DOCTYPE, {"month": month, "docstatus": 0}, "name")
	doc = frappe.get_doc(REPORT_DOCTYPE, draft) if draft else frappe.new_doc(REPORT_DOCTYPE)
	doc.month = month
	populate(doc)
	doc.flags.ignore_permissions = True
	if doc.is_new():
		doc.insert()
	else:
		doc.save()
	doc.submit()
	return doc.name


def populate(doc) -> None:
	"""Fill every figure on ``doc`` for its month."""
	from_date, to_date, start, end = month_bounds(doc.month)
	settings = get_settings()
	figures = collect_figures(start, end, from_date, to_date)
	doc.update(
		{
			"from_date": from_date,
			"to_date": to_date,
			"instance_label": settings.get("instance_label"),
			"prepared_on": now_datetime(),
			**{k: v for k, v in figures.items() if k != "incident_rows"},
		}
	)
	doc.set("incident_details", [])
	for row in figures["incident_rows"]:
		doc.append("incident_details", row)
	doc.summary = build_summary(doc)


def _safe(label: str, func, default):
	try:
		return func()
	except Exception:
		frappe.log_error(title=f"Hostel360: monthly report - {label} unavailable", message=frappe.get_traceback())
		return default


def _exists(doctype: str) -> bool:
	try:
		return frappe.db.table_exists(doctype)
	except Exception:
		return False


def collect_figures(start, end, from_date, to_date) -> dict:
	from hostel360.hostel_operations import metrics
	from hostel360.hostel_operations.support import sla_compliance

	figures = {}

	uptime = _safe("uptime", lambda: metrics.compute_uptime(start, end), {})
	figures["uptime_percent"] = uptime.get("uptime_percent")
	figures["downtime_minutes"] = cint(uptime.get("downtime_minutes"))
	figures["unmonitored_minutes"] = cint(uptime.get("unmonitored_minutes"))
	figures["monitoring_coverage_percent"] = flt(uptime.get("coverage_percent"), 2)
	figures["uptime_source"] = uptime.get("source") or ""

	incidents = _safe("incidents", lambda: _critical_alerts(start, end), [])
	figures["incidents"] = len(incidents)
	figures["critical_alerts"] = sum(cint(a.occurrence_count) or 1 for a in incidents)
	figures["incident_rows"] = [
		{
			"alert": a.name,
			"title": a.title,
			"severity": a.severity,
			"category": a.category,
			"first_seen": a.first_seen,
			"resolved_on": a.resolved_on,
			"duration_minutes": _duration(a, end),
			"is_outage": cint(a.is_outage),
		}
		for a in incidents[:MAX_INCIDENT_ROWS]
	]

	for prefix, metric in (("cpu", "cpu_percent"), ("memory", "memory_percent")):
		stats = _safe(metric, lambda metric=metric: metrics.metric_stats(metric, start, end), {})
		figures[f"avg_{prefix}_percent"] = flt(stats.get("avg"))
		figures[f"peak_{prefix}_percent"] = flt(stats.get("peak"))
	disk = _safe("disk", lambda: metrics.metric_stats("disk_percent", start, end), {})
	if not disk.get("samples"):
		disk = _safe("disk", lambda: metrics.metric_stats("sites_disk_percent", start, end), {})
	figures["disk_used_percent"] = flt(disk.get("latest"))

	figures["security_events"] = _safe(
		"security events",
		lambda: frappe.db.count(ALERT_DOCTYPE, {"category": "Security", "first_seen": ("between", [start, end])}),
		0,
	)
	figures["malware_detections"] = _safe("malware", lambda: _malware_detections(start, end), 0)
	figures["waf_blocks"] = cint(_safe("waf", lambda: metrics.metric_sum("waf_blocks", start, end), 0))

	backups = _safe("backups", lambda: _backup_counts(start, end), {})
	figures["backups_succeeded"] = backups.get("Success", 0)
	figures["backups_failed"] = backups.get("Failed", 0)
	figures["last_restore_test_on"] = _safe("restore tests", lambda: _last_restore_test(end), None)

	tickets = _safe("tickets", lambda: sla_compliance(start, end), {})
	figures["tickets_raised"] = tickets.get("raised", 0)
	figures["tickets_resolved"] = tickets.get("resolved", 0)
	figures["sla_compliance_percent"] = tickets.get("compliance_percent", 100.0)

	figures["active_students"] = _safe("students", _active_students, 0)
	figures["portal_logins"] = _safe("portal logins", lambda: _portal_logins(start, end), 0)
	payments = _safe("payments", lambda: _online_payments(from_date, to_date), {})
	figures["online_payments_count"] = payments.get("count", 0)
	figures["online_payments_amount"] = payments.get("amount", 0.0)
	return figures


def _critical_alerts(start, end) -> list:
	return frappe.get_all(
		ALERT_DOCTYPE,
		filters=[["severity", "=", CRITICAL], ["first_seen", ">=", start], ["first_seen", "<", end]],
		fields=[
			"name",
			"title",
			"severity",
			"category",
			"first_seen",
			"resolved_on",
			"occurrence_count",
			"is_outage",
		],
		order_by="first_seen asc",
	)


def _duration(alert, end) -> int:
	finish = get_datetime(alert.resolved_on) if alert.resolved_on else min(now_datetime(), end)
	return max(int((finish - get_datetime(alert.first_seen)).total_seconds() // 60), 0)


def _malware_detections(start, end) -> int:
	from hostel360.hostel_operations import metrics

	# Detections only: "malware-detected-<hash>" from upload scanning and maldet reports from
	# the host agent, not "malware-scanner-unreachable" and similar availability alerts.
	alerts = frappe.get_all(
		ALERT_DOCTYPE,
		filters=[["category", "=", "Security"], ["first_seen", ">=", start], ["first_seen", "<", end]],
		or_filters=[["alert_key", "like", "%malware%detect%"], ["alert_key", "like", "%maldet%"]],
		fields=["occurrence_count"],
	)
	return sum(cint(a.occurrence_count) or 1 for a in alerts) + cint(metrics.metric_sum("malware_detections", start, end))


def _backup_counts(start, end) -> dict:
	rows = frappe.get_all(
		BACKUP_DOCTYPE,
		filters=[["backup_type", "in", BACKUP_TYPES], ["finished_at", ">=", start], ["finished_at", "<", end]],
		fields=["status", {"COUNT": "*", "as": "count"}],
		group_by="status",
	)
	return {row.status: cint(row["count"]) for row in rows}


def _last_restore_test(end):
	rows = frappe.get_all(
		BACKUP_DOCTYPE,
		filters=[["backup_type", "=", "Restore Test"], ["status", "=", "Success"], ["finished_at", "<", end]],
		fields=["finished_at"],
		order_by="finished_at desc",
		limit=1,
	)
	return rows[0].finished_at if rows else None


def _active_students() -> int:
	if not _exists("Hostel Student"):
		return 0
	return frappe.db.count("Hostel Student", {"status": "Active"})


def _portal_logins(start, end) -> int:
	log = frappe.qb.DocType("Activity Log")
	has_role = frappe.qb.DocType("Has Role")
	from pypika.functions import Count

	portal_users = (
		frappe.qb.from_(has_role)
		.select(has_role.parent)
		.where((has_role.parenttype == "User") & (has_role.role.isin(list(PORTAL_ROLES))))
	)
	result = (
		frappe.qb.from_(log)
		.select(Count("*"))
		.where(
			(log.operation == "Login")
			& (log.status == "Success")
			& (log.creation >= start)
			& (log.creation < end)
			& (log.user.isin(portal_users))
		)
	).run()
	return cint(result[0][0]) if result else 0


def _online_payments(from_date, to_date) -> dict:
	if not _exists("Hostel Payment"):
		return {}
	payment = frappe.qb.DocType("Hostel Payment")
	from pypika.functions import Count, Sum

	row = (
		frappe.qb.from_(payment)
		.select(Count("*").as_("count"), Sum(payment.amount).as_("amount"))
		.where(
			(payment.docstatus == 1)
			& (payment.mode_of_payment == "Online")
			& (payment.payment_date >= from_date)
			& (payment.payment_date <= to_date)
		)
	).run(as_dict=True)[0]
	return {"count": cint(row["count"]), "amount": flt(row.amount, 2)}


def build_summary(doc) -> str:
	from frappe.utils import escape_html

	from hostel360.utils.jinja import format_inr

	parts = []
	if doc.uptime_percent is not None and doc.get("uptime_source") != "No availability data":
		met = flt(doc.uptime_percent) >= UPTIME_TARGET
		parts.append(
			_(
				"Availability was {0}% against the contracted {1}% ({2}), with {3} minutes of downtime and {4} critical incidents."
			).format(
				flt(doc.uptime_percent, 3),
				UPTIME_TARGET,
				_("target met") if met else _("target missed"),
				cint(doc.downtime_minutes),
				cint(doc.incidents),
			)
		)
		if cint(doc.get("unmonitored_minutes")):
			parts.append(
				_(
					"{0} of those minutes had no availability measurement (monitoring covered {1}% of the month) and are counted as downtime."
				).format(cint(doc.unmonitored_minutes), flt(doc.get("monitoring_coverage_percent"), 2))
			)
	else:
		parts.append(
			_("No availability measurements were recorded this month; {0} critical incidents were logged.").format(
				cint(doc.incidents)
			)
		)
	parts.append(
		_(
			"The server averaged {0}% CPU (peak {1}%) and {2}% memory (peak {3}%); disk usage stood at {4}%."
		).format(
			flt(doc.avg_cpu_percent, 1),
			flt(doc.peak_cpu_percent, 1),
			flt(doc.avg_memory_percent, 1),
			flt(doc.peak_memory_percent, 1),
			flt(doc.disk_used_percent, 1),
		)
	)
	parts.append(
		_("Security: {0} security events, {1} malware detections and {2} requests blocked by the web application firewall.").format(
			cint(doc.security_events), cint(doc.malware_detections), cint(doc.waf_blocks)
		)
	)
	restore = (
		_("the last restore test was on {0}").format(format_date(doc.last_restore_test_on))
		if doc.last_restore_test_on
		else _("no restore test has been recorded")
	)
	parts.append(
		_("Backups: {0} succeeded and {1} failed; {2}.").format(
			cint(doc.backups_succeeded), cint(doc.backups_failed), restore
		)
	)
	parts.append(
		_("Support: {0} tickets raised and {1} resolved, with {2}% meeting the response and resolution SLAs.").format(
			cint(doc.tickets_raised), cint(doc.tickets_resolved), flt(doc.sla_compliance_percent, 1)
		)
	)
	parts.append(
		_("Usage: {0} active residents, {1} portal logins and {2} online payments totalling {3}.").format(
			cint(doc.active_students),
			cint(doc.portal_logins),
			cint(doc.online_payments_count),
			format_inr(doc.online_payments_amount),
		)
	)
	return "".join(f"<p>{escape_html(part)}</p>" for part in parts)


# Delivery -----------------------------------------------------------------


def institution_recipients(settings=None) -> list[str]:
	"""Institution officials: Report Recipients, else the hostel office e-mail address."""
	settings = settings or get_settings()
	recipients = parse_emails(settings.get("report_recipients"))
	if not recipients and frappe.db.exists("DocType", "Hostel Settings"):
		try:
			recipients = parse_emails(frappe.db.get_single_value("Hostel Settings", "office_email"))
		except Exception:
			recipients = []
	return recipients


def report_recipients() -> list[str]:
	"""Everyone the report goes to; empty when nobody at the institution would receive it."""
	settings = get_settings()
	recipients = institution_recipients(settings)
	if not recipients:
		return []
	for email in level_one_emails(settings):
		if email not in recipients:
			recipients.append(email)
	return recipients


def render_pdf(name: str) -> tuple[bytes, str]:
	"""PDF of the report; falls back to HTML when the PDF renderer is unavailable."""
	try:
		pdf = frappe.get_print(REPORT_DOCTYPE, name, print_format=PRINT_FORMAT, as_pdf=True, no_letterhead=1)
		return pdf, f"{name}.pdf"
	except Exception:
		frappe.log_error(title="Hostel360: monthly report PDF could not be rendered", message=frappe.get_traceback())
		html = frappe.get_print(REPORT_DOCTYPE, name, print_format=PRINT_FORMAT, no_letterhead=1)
		return html.encode(), f"{name}.html"


def send_report(name: str) -> bool:
	"""E-mail the report; returns False (and leaves it unsent) when nobody at the institution would get it."""
	from hostel360.hostel_operations.alerts import raise_alert, resolve_alert

	doc = frappe.get_doc(REPORT_DOCTYPE, name)
	recipients = report_recipients()
	if not recipients:
		# A copy to the HyFib helpdesk alone is not delivery to the institution (TR 13), so
		# the report stays unsent and is retried once someone fills in the recipients.
		raise_alert(
			NO_RECIPIENTS_ALERT_KEY,
			"Monthly performance report has no institution recipients",
			WARNING,
			"SLA",
			description=_(
				"{0} could not be sent: add the institution officials to Report Recipients in Ops Alert Settings "
				"(or set the office e-mail in Hostel Settings). It is sent automatically once they are added."
			).format(name),
		)
		return False

	content, filename = render_pdf(name)
	month_label = getdate(doc.from_date).strftime("%B %Y")
	frappe.sendmail(
		recipients=recipients,
		subject=_("Hostel360 monthly performance report - {0} ({1})").format(
			month_label, doc.instance_label or "Hostel360"
		),
		message=_email_body(doc, month_label),
		attachments=[{"fname": filename, "fcontent": content}],
		reference_doctype=REPORT_DOCTYPE,
		reference_name=name,
		add_unsubscribe_link=0,
	)
	doc.db_set({"sent_on": now_datetime(), "sent_to": "\n".join(recipients)}, update_modified=False)
	resolve_alert(NO_RECIPIENTS_ALERT_KEY, notes=_("{0} was sent to {1}.").format(name, ", ".join(recipients)))
	return True


def delivery_status(now=None) -> dict:
	"""Where the previous month's report stands: ``{month, name, sent_on, due_day, applicable}``.

	Not applicable before the 07:00 run on the 1st, or when the installation was not yet
	measuring anything during that month (nothing to report on).
	"""
	from hostel360.hostel_operations.metrics import monitoring_started_at

	now = get_datetime(now) if now else now_datetime()
	month = previous_month(now)
	_from_date, _to_date, _start, end = month_bounds(month)
	row = frappe.db.get_value(REPORT_DOCTYPE, {"month": month, "docstatus": 1}, ["name", "sent_on"], as_dict=True)
	started = monitoring_started_at()
	applicable = bool(row) or bool(started and started < end)
	if now.day == 1 and now.hour < 8:
		applicable = False
	return {
		"month": month,
		"name": row.name if row else None,
		"sent_on": row.sent_on if row else None,
		"applicable": applicable,
		"day": now.day,
	}


def catch_up_monthly_report(now=None) -> str | None:
	"""Generate and send last month's report if the 1st's run did not get it out."""
	status = delivery_status(now)
	if not status["applicable"] or status["sent_on"]:
		return None
	return generate_monthly_performance_report(month=status["month"], send=True)


def _email_body(doc, month_label: str) -> str:
	from frappe.utils import escape_html

	uptime = (
		f"{flt(doc.uptime_percent, 3)}%"
		if doc.uptime_percent is not None and doc.get("uptime_source") != "No availability data"
		else _("not measured")
	)
	if cint(doc.get("unmonitored_minutes")) and doc.uptime_percent is not None:
		uptime += " " + _("({0} unmonitored minutes counted as downtime)").format(cint(doc.unmonitored_minutes))
	return (
		f"<p>{escape_html(_('Dear Sir / Madam,'))}</p>"
		f"<p>{escape_html(_('Please find attached the Hostel360 server performance report for {0}.').format(month_label))}</p>"
		"<table style='border-collapse:collapse'>"
		f"<tr><td style='padding:4px 12px 4px 0'>{escape_html(_('Availability'))}</td><td><b>{escape_html(uptime)}</b></td></tr>"
		f"<tr><td style='padding:4px 12px 4px 0'>{escape_html(_('Critical incidents'))}</td><td><b>{cint(doc.incidents)}</b></td></tr>"
		f"<tr><td style='padding:4px 12px 4px 0'>{escape_html(_('Backups'))}</td><td><b>{cint(doc.backups_succeeded)} / {cint(doc.backups_succeeded) + cint(doc.backups_failed)}</b></td></tr>"
		f"<tr><td style='padding:4px 12px 4px 0'>{escape_html(_('Support SLA compliance'))}</td><td><b>{flt(doc.sla_compliance_percent, 1)}%</b></td></tr>"
		"</table>"
		f"<p>{escape_html(_('Regards,'))}<br>{escape_html(_('HyFib Solutions Private Limited - Hostel360 Support'))}</p>"
	)


# Desk actions -------------------------------------------------------------


def _require_operations() -> None:
	from hostel360.utils.context import has_any_role

	if not has_any_role({OPERATIONS_ROLE, "System Manager"}):
		frappe.throw(_("Only HyFib Operations can do this."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def generate_report(month: str | None = None) -> str:
	_require_operations()
	return generate_monthly_performance_report(month=month or previous_month(), send=False)


@frappe.whitelist(methods=["POST"])
def send_report_now(name: str) -> dict:
	_require_operations()
	if frappe.db.get_value(REPORT_DOCTYPE, name, "docstatus") != 1:
		frappe.throw(_("Submit the report before sending it."))
	sent = send_report(name)
	return {"sent": sent, "recipients": report_recipients() if sent else []}


def _commit() -> None:
	if not frappe.flags.in_test:
		frappe.db.commit()  # nosemgrep
