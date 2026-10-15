# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Security incidents and the six-hour CERT-In reporting clock.

The CERT-In directions of 28 April 2022 require reportable cyber security incidents to be
reported to CERT-In within six hours of being noticed, and the Hostel360 contract adds the
institution to the same deadline. An incident records when it was detected, when each report
went out, and why it was closed. The health checks (``check_security_incidents``) raise a
warning alert four hours after detection and a critical one at six while a report is still
outstanding; recording both reports, or assessing the incident as not reportable, resolves
that alert.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, get_datetime, now_datetime

from hostel360.hostel_operations.constants import (
	ALERT_DOCTYPE,
	CERT_IN_REPORTING_HOURS,
	SECURITY_INCIDENT_DOCTYPE,
)
from hostel360.setup.roles import OPERATIONS_ROLE

STATUS_OPEN = "Open"
STATUS_REPORTED = "Reported"
STATUS_CLOSED = "Closed"
STATUS_NOT_REPORTABLE = "Not Reportable"
# Agent and desk clocks drift a little; a time this far ahead is a typing mistake.
CLOCK_SKEW_MINUTES = 5
ALERT_SEVERITY = {"Critical": "Critical", "Warning": "High", "Info": "Medium"}


def reporting_alert_key(name: str) -> str:
	return f"security.incident.{name}.reporting"


class SecurityIncident(Document):
	def validate(self):
		self._validate_times()
		self.cert_in_deadline = add_to_date(get_datetime(self.detected_on), hours=CERT_IN_REPORTING_HOURS)
		self._set_status()
		self.reporting_sla_breached = 1 if self._deadline_missed() else 0
		if (self.reported_to_certin_on or self.reported_to_institution_on) and not self.reported_by:
			self.reported_by = frappe.session.user

	def _validate_times(self):
		now = now_datetime()
		latest = add_to_date(now, minutes=CLOCK_SKEW_MINUTES)
		if not self.detected_on:
			self.detected_on = now
		detected = get_datetime(self.detected_on)
		if detected > latest:
			frappe.throw(_("Detected On cannot be in the future."))
		for fieldname in ("reported_to_certin_on", "reported_to_institution_on"):
			value = self.get(fieldname)
			if not value:
				continue
			label = _(self.meta.get_label(fieldname))
			if get_datetime(value) > latest:
				frappe.throw(_("{0} cannot be in the future.").format(label))
			if get_datetime(value) < detected:
				frappe.throw(_("{0} cannot be before the incident was detected.").format(label))

	def _reports_complete(self) -> bool:
		return bool(self.reported_to_certin_on and self.reported_to_institution_on)

	def _set_status(self):
		if self.status == STATUS_NOT_REPORTABLE:
			self.cert_in_reportable = 0
		if not self.cert_in_reportable:
			if not (self.assessment_notes or "").strip():
				frappe.throw(
					_("Explain in the assessment notes why this incident is not reportable to CERT-In."),
					title=_("Assessment Required"),
				)
			if self.status != STATUS_CLOSED:
				self.status = STATUS_NOT_REPORTABLE
		elif self.status == STATUS_NOT_REPORTABLE:
			self.status = STATUS_OPEN

		if self.cert_in_reportable and self.status in (STATUS_OPEN, STATUS_REPORTED):
			self.status = STATUS_REPORTED if self._reports_complete() else STATUS_OPEN

		if self.status == STATUS_CLOSED:
			if self.cert_in_reportable and not self._reports_complete():
				frappe.throw(
					_("Record when CERT-In and the institution were informed before closing the incident."),
					title=_("Reports Missing"),
				)
			if not (self.assessment_notes or "").strip():
				frappe.throw(_("Add assessment notes before closing the incident."), title=_("Assessment Required"))
			self.closed_on = self.closed_on or now_datetime()
		else:
			self.closed_on = None

	def _deadline_missed(self) -> bool:
		if not self.cert_in_reportable:
			return False
		deadline = get_datetime(self.cert_in_deadline)
		for fieldname in ("reported_to_certin_on", "reported_to_institution_on"):
			value = self.get(fieldname)
			if (get_datetime(value) if value else now_datetime()) > deadline:
				return True
		return False

	def on_update(self):
		if self.status != STATUS_OPEN:
			# Nothing is outstanding any more; stop the deadline alert.
			from hostel360.hostel_operations.alerts import resolve_alert

			resolve_alert(
				reporting_alert_key(self.name),
				notes=_("Security Incident {0} is {1}.").format(self.name, _(self.status)),
				source="Security",
			)

	def after_insert(self):
		if self.ops_alert and frappe.db.exists(ALERT_DOCTYPE, self.ops_alert):
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Info",
					"reference_doctype": ALERT_DOCTYPE,
					"reference_name": self.ops_alert,
					"content": _("Security Incident {0} declared by {1}.").format(self.name, frappe.session.user),
				}
			).insert(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def declare_from_alert(alert: str) -> str:
	"""Open a Security Incident for an Ops Alert, or return the one already open for it."""
	from hostel360.utils.context import has_any_role

	if not has_any_role({OPERATIONS_ROLE, "System Manager"}):
		frappe.throw(_("Only HyFib Operations can declare security incidents."), frappe.PermissionError)
	frappe.has_permission(SECURITY_INCIDENT_DOCTYPE, "create", throw=True)

	source = frappe.get_doc(ALERT_DOCTYPE, alert)
	source.check_permission("read")
	existing = frappe.db.get_value(
		SECURITY_INCIDENT_DOCTYPE, {"ops_alert": source.name, "status": ("!=", STATUS_CLOSED)}, "name"
	)
	if existing:
		return existing

	description = frappe.utils.escape_html(source.description or source.title or "")
	incident = frappe.get_doc(
		{
			"doctype": SECURITY_INCIDENT_DOCTYPE,
			"title": source.title,
			"ops_alert": source.name,
			"severity": ALERT_SEVERITY.get(source.severity, "High"),
			"category": _guess_category(source),
			"detected_on": source.first_seen or now_datetime(),
			"description": f"<p>{description}</p>" if description else "",
			"status": STATUS_OPEN,
		}
	)
	incident.insert()
	return incident.name


def _guess_category(alert) -> str:
	"""A starting point from the alert key; the responder confirms or corrects it."""
	text = f"{alert.alert_key} {alert.title}".lower()
	if "malware" in text or "maldet" in text or "clamav" in text:
		return "Malicious code (virus, worm, trojan, bot, spyware, ransomware, cryptominer)"
	if "login" in text or "signature" in text or "access" in text:
		return "Unauthorised access to IT systems or data"
	if "ddos" in text or "waf" in text:
		return "Denial of service (DoS or DDoS)"
	if "payment" in text or "razorpay" in text or "payu" in text:
		return "Attack on digital payment systems"
	return "Attack on applications (e-governance, e-commerce and similar)"
