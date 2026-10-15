# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_url, now_datetime

from hostel360.hostel_operations.constants import (
	CHANNEL_EMAIL,
	MOBILE_CHANNELS,
	WEBHOOK_CHANNELS,
)
from hostel360.hostel_operations.settings import parse_emails, parse_mobiles
from hostel360.setup.roles import OPERATIONS_ROLE

INGEST_METHOD = "/api/method/hostel360.api.ops.ingest"
OCI_METHOD = "/api/method/hostel360.api.ops.oci_notification"


class OpsAlertSettings(Document):
	def validate(self):
		self._validate_channels()
		self._validate_levels()
		self._validate_thresholds()
		if self.report_recipients:
			self.report_recipients = "\n".join(parse_emails(self.report_recipients, throw=True))
		if self.heartbeat_url and not self._url_allowed(self.heartbeat_url):
			frappe.throw(_("The heartbeat URL must start with https://."))

	def _url_allowed(self, url: str) -> bool:
		url = (url or "").strip()
		return url.startswith("https://") or (self.environment == "Development" and url.startswith("http://"))

	def _validate_channels(self):
		for row in self.channels:
			label = _("Channel row {0}").format(row.idx)
			if row.channel_type == CHANNEL_EMAIL:
				if not parse_emails(row.target, throw=True):
					frappe.throw(_("{0}: enter at least one e-mail address.").format(label))
				row.target = ", ".join(parse_emails(row.target))
			elif row.channel_type in MOBILE_CHANNELS:
				if not parse_mobiles(row.target, throw=True):
					frappe.throw(_("{0}: enter at least one mobile number.").format(label))
				row.target = ", ".join(parse_mobiles(row.target))
			elif row.channel_type in WEBHOOK_CHANNELS:
				url = row.secret_url
				if not url:
					frappe.throw(_("{0}: the webhook URL is required.").format(label))
				# A masked value means the stored URL is unchanged.
				if not row.is_dummy_password(url) and not self._url_allowed(url):
					frappe.throw(_("{0}: webhook URLs must start with https://.").format(label))

	def _validate_levels(self):
		seen = set()
		for row in sorted(self.escalation_levels, key=lambda r: cint(r.level)):
			level = cint(row.level)
			if level < 1:
				frappe.throw(_("Escalation row {0}: the level must be 1 or more.").format(row.idx))
			if level in seen:
				frappe.throw(_("Escalation level {0} is listed twice.").format(level))
			seen.add(level)
			if cint(row.after_minutes) < 0:
				frappe.throw(_("Escalation level {0}: minutes cannot be negative.").format(level))
			row.notify_emails = "\n".join(parse_emails(row.notify_emails, throw=True))
			row.notify_mobiles = "\n".join(parse_mobiles(row.notify_mobiles, throw=True))

		previous = -1
		for row in sorted(self.escalation_levels, key=lambda r: cint(r.level)):
			if cint(row.after_minutes) < previous:
				frappe.throw(
					_("Escalation level {0} starts before the level above it. Later levels should wait longer.").format(
						row.level
					)
				)
			previous = cint(row.after_minutes)

	def _validate_thresholds(self):
		if cint(self.disk_warning_percent) and cint(self.disk_critical_percent):
			if cint(self.disk_warning_percent) >= cint(self.disk_critical_percent):
				frappe.throw(_("The disk warning level must be below the critical level."))
		for fieldname in ("disk_warning_percent", "disk_critical_percent", "message_failure_percent"):
			if cint(self.get(fieldname)) > 100:
				frappe.throw(_("{0} cannot be more than 100%.").format(_(self.meta.get_label(fieldname))))

	# Desk actions ---------------------------------------------------------

	@frappe.whitelist()
	def send_test_notification(self):
		_require_operations()
		from hostel360.hostel_operations import notification

		settings = frappe.get_doc("Ops Alert Settings")
		alert = frappe._dict(
			name=None,
			alert_key="ops.test",
			title=_("Test notification from Hostel360"),
			severity="Critical",
			category="Other",
			source="App",
			status="Open",
			description=_("Sent from Ops Alert Settings by {0} to check the notification channels.").format(
				frappe.session.user
			),
			first_seen=now_datetime(),
			last_seen=now_datetime(),
			occurrence_count=1,
		)
		message = notification.build_message(alert, settings, "test", [])
		message["url"] = get_url("/desk/ops-alert-settings")
		targets = notification.resolve_targets(settings, alert, "test", [])
		results = notification.send_to_targets(targets, message, settings)
		return [
			{"channel": r["channel_type"], "target": r["label"], "ok": r["ok"], "info": r["info"]} for r in results
		]

	@frappe.whitelist()
	def run_health_checks_now(self):
		_require_operations()
		from hostel360.hostel_operations import health

		summary = health._run_and_apply()
		return summary["results"]

	@frappe.whitelist()
	def get_integration_details(self):
		_require_operations()
		token = self.get_password("oci_notification_token", raise_exception=False) or ""
		return {
			"ingest_url": get_url(INGEST_METHOD),
			"oci_url": get_url(f"{OCI_METHOD}?token={token}") if token else None,
			"ingest_secret_set": bool(self.get_password("ingest_secret", raise_exception=False)),
		}

	@frappe.whitelist()
	def reveal_ingest_secret(self):
		_require_operations()
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": self.doctype,
				"reference_name": self.name,
				"content": _("Ingest secret viewed by {0}").format(frappe.session.user),
			}
		).insert(ignore_permissions=True)
		return self.get_password("ingest_secret", raise_exception=False)

	@frappe.whitelist()
	def rotate_secret(self, fieldname: str):
		_require_operations()
		from hostel360.hostel_operations.setup import rotate_secret

		rotate_secret(fieldname)
		return {"rotated": fieldname}


def _require_operations() -> None:
	from hostel360.utils.context import has_any_role

	if not has_any_role({OPERATIONS_ROLE, "System Manager"}):
		frappe.throw(_("Only HyFib Operations can do this."), frappe.PermissionError)
