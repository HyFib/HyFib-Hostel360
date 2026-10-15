"""Fixtures shared by the operations tests: known settings and a delivery recorder."""

from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from hostel360.hostel_operations import channels

TEST_INGEST_SECRET = "a3f1c0de9b8e4d7f6a5b4c3d2e1f00112233445566778899aabbccddeeff0011"
TEST_OCI_TOKEN = "oci-test-token-6f2d9c1b7a"

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
