"""Fixtures shared by the operations tests: known settings and a delivery recorder."""

from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from hostel360.hostel_operations import channels

TEST_INGEST_SECRET = "a3f1c0de9b8e4d7f6a5b4c3d2e1f00112233445566778899aabbccddeeff0011"  # noqa: S105
TEST_OCI_TOKEN = "oci-test-token-6f2d9c1b7a"  # noqa: S105

LEVEL_ONE_EMAIL = "l1.helpdesk@example.com"
LEVEL_TWO_EMAIL = "l2.lead@example.com"
LEVEL_THREE_EMAIL = "l3.director@example.com"
LEVEL_ONE_MOBILE = "9000000001"
LEVEL_TWO_MOBILE = "9000000002"
LEVEL_THREE_MOBILE = "9000000003"
SLACK_URL = "https://hooks.slack.example.com/services/T000/B000/secret"
CHANNEL_EMAIL = "ops.channel@example.com"


def clear_settings_cache() -> None:
	frappe.clear_document_cache("Ops Alert Settings", "Ops Alert Settings")


def configure_settings(**overrides):
	"""Ops Alert Settings with a predictable channel list and escalation matrix."""
	settings = frappe.get_doc("Ops Alert Settings")
	settings.update(
		{
			"enabled": 1,
			"environment": "Production",
			"instance_label": "Test Hostel360",
			"critical_renotify_minutes": 60,
			"report_recipients": "",
			"heartbeat_url": "",
			"ingest_secret": TEST_INGEST_SECRET,
			"oci_notification_token": TEST_OCI_TOKEN,
		}
	)
	settings.set(
		"channels",
		[
			{
				"channel_type": "Slack Webhook",
				"target": "#ops-test",
				"secret_url": SLACK_URL,
				"min_severity": "Warning",
				"enabled": 1,
			},
			{"channel_type": "Email", "target": CHANNEL_EMAIL, "min_severity": "Critical", "enabled": 1},
		],
	)
	settings.set(
		"escalation_levels",
		[
			{
				"level": 1,
				"label": "Helpdesk",
				"after_minutes": 0,
				"min_severity": "Info",
				"notify_emails": LEVEL_ONE_EMAIL,
				"notify_mobiles": LEVEL_ONE_MOBILE,
				"mobile_min_severity": "Critical",
				"mobile_channel": "SMS",
				"outage_immediate": 0,
			},
			{
				"level": 2,
				"label": "Technical lead",
				"after_minutes": 120,
				"min_severity": "Warning",
				"notify_emails": LEVEL_TWO_EMAIL,
				"notify_mobiles": LEVEL_TWO_MOBILE,
				"mobile_min_severity": "Critical",
				"mobile_channel": "SMS",
				"outage_immediate": 1,
			},
			{
				"level": 3,
				"label": "Director",
				"after_minutes": 180,
				"min_severity": "Warning",
				"notify_emails": LEVEL_THREE_EMAIL,
				"notify_mobiles": LEVEL_THREE_MOBILE,
				"mobile_min_severity": "Critical",
				"mobile_channel": "SMS",
				"outage_immediate": 1,
			},
		],
	)
	settings.update(overrides)
	settings.flags.ignore_permissions = True
	settings.save()
	clear_settings_cache()
	return settings


class DeliveryRecorder:
	"""Captures what would have been sent instead of sending it."""

	def __init__(self):
		self.emails = []
		self.texts = []
		self.webhooks = []
		self._patches = []

	def __enter__(self):
		def fake_email(recipients, message):
			self.emails.append({"recipients": list(recipients), "message": message})
			return True, "queued"

		def fake_text(channel, mobile, message):
			self.texts.append({"channel": channel, "mobile": mobile, "message": message})
			return True, "sent"

		def fake_post(url, payload, headers=None, signing_secret=None, allow_http=False):
			self.webhooks.append({"url": url, "payload": payload, "headers": headers or {}})
			return True, "HTTP 200"

		self._patches = [
			patch.object(channels, "send_email", side_effect=fake_email),
			patch.object(channels, "send_text", side_effect=fake_text),
			patch.object(channels, "post_json", side_effect=fake_post),
		]
		for p in self._patches:
			p.start()
		return self

	def __exit__(self, *exc):
		for p in reversed(self._patches):
			p.stop()
		return False

	def clear(self):
		self.emails.clear()
		self.texts.clear()
		self.webhooks.clear()

	def for_alert(self, alert_name: str) -> dict:
		return {
			"emails": [e for e in self.emails if e["message"].get("alert") == alert_name],
			"texts": [t for t in self.texts if t["message"].get("alert") == alert_name],
			"webhooks": [w for w in self.webhooks if _webhook_alert(w) == alert_name],
		}

	def events(self, alert_name: str) -> list[str]:
		seen = []
		for item in self.for_alert(alert_name)["emails"]:
			seen.append(item["message"]["event"])
		return seen

	def email_recipients(self, alert_name: str) -> set[str]:
		recipients = set()
		for item in self.for_alert(alert_name)["emails"]:
			recipients.update(item["recipients"])
		return recipients

	def mobiles(self, alert_name: str) -> set[str]:
		return {t["mobile"] for t in self.for_alert(alert_name)["texts"]}


def _webhook_alert(webhook: dict):
	payload = webhook["payload"]
	if "alert" in payload and isinstance(payload["alert"], dict):
		return payload["alert"].get("name")
	for block in payload.get("blocks") or []:
		for element in block.get("elements") or []:
			url = element.get("url") or ""
			if "/ops-alert/" in url:
				return url.rsplit("/", 1)[-1]
	return None


def unique_key(prefix: str = "test") -> str:
	return f"{prefix}.{frappe.generate_hash(length=10)}"


def age_alert(name: str, minutes: int) -> None:
	"""Pretend the alert was first seen ``minutes`` ago."""
	from frappe.utils import add_to_date

	first_seen = add_to_date(now_datetime(), minutes=-minutes)
	frappe.db.set_value("Ops Alert", name, {"first_seen": first_seen}, update_modified=False)


# Independent alert transactions ------------------------------------------------
# In production raise_alert/resolve_alert commit on a connection of their own; tests run
# them inline so everything is rolled back with the test. The helpers below exercise the
# production path and clean up what it committed.


class IndependentAlerts:
	"""Run alerting on its own connection for the duration of the block, then delete every
	Ops Alert (with its comments and versions) committed for ``keys`` during the block."""

	def __init__(self, *keys: str):
		self.keys = list(keys)
		self.started = None
		self.existing: set[str] = set()

	def __enter__(self):
		self.started = now_datetime()
		self.existing = {row[0] for row in committed_rows("select name from `tabOps Alert`")}
		frappe.flags.hostel360_independent_alerts = True
		return self

	def __exit__(self, *exc):
		frappe.flags.hostel360_independent_alerts = False
		names = [
			row[0]
			for row in committed_rows(
				"select name from `tabOps Alert` where alert_key in %(keys)s",
				{"keys": tuple(self.keys or [""])},
			)
			if row[0] not in self.existing
		]
		delete_committed_alerts(names)
		delete_committed_error_logs(self.keys, self.started)
		return False

	def committed(self, key: str) -> list[dict]:
		return committed_rows(
			"select name, status, occurrence_count, open_key from `tabOps Alert` where alert_key = %(key)s",
			{"key": key},
			as_dict=True,
		)


def committed_rows(query: str, values=None, as_dict=False):
	"""Read through a fresh connection: what other processes see, not this test's transaction."""
	from hostel360.hostel_operations import transaction

	db = transaction.open_connection()
	try:
		return db.sql(query, values or {}, as_dict=as_dict)
	finally:
		db.close()


def delete_committed_alerts(names: list[str]) -> None:
	if not names:
		return
	from hostel360.hostel_operations import transaction

	db = transaction.open_connection()
	try:
		values = {"names": tuple(names)}
		db.sql(
			"delete from `tabComment` where reference_doctype='Ops Alert' and reference_name in %(names)s",
			values,
		)
		db.sql("delete from `tabVersion` where ref_doctype='Ops Alert' and docname in %(names)s", values)
		db.sql("delete from `tabOps Alert` where name in %(names)s", values)
		db.commit()
	finally:
		db.close()


def delete_committed_error_logs(keys: list[str], since) -> None:
	"""Remove the Error Log rows that failure-path tests commit through the alert connection."""
	from hostel360.hostel_operations import transaction

	if not keys:
		return
	db = transaction.open_connection()
	try:
		for key in keys:
			db.sql(
				"delete from `tabError Log` where creation >= %(since)s and error like %(pattern)s",
				{"since": since, "pattern": f"%Alert key: {key}%"},
			)
		db.commit()
	finally:
		db.close()
