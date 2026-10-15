"""Delivery of alert messages to e-mail, text messages and chat webhooks.

Every sender returns ``(ok, info)`` and never raises: one broken channel (an expired Teams
webhook, a mail server outage) must not stop the other channels from paging people.
Messages are plain dictionaries built by ``notification.build_message``.
"""

import hashlib
import hmac
import json
import time

import frappe
from frappe.utils import escape_html

from hostel360.hostel_operations.constants import (
	CHANNEL_GOOGLE_CHAT,
	CHANNEL_SLACK,
	CHANNEL_TEAMS,
	CHANNEL_WEBHOOK,
	CRITICAL,
	INFO,
	WARNING,
)

HTTP_TIMEOUT_SECONDS = 5
USER_AGENT = "HyFib-Hostel360-Alerts/1.0"

SEVERITY_COLOURS = {CRITICAL: "#c62828", WARNING: "#ef6c00", INFO: "#1565c0"}
RESOLVED_COLOUR = "#2e7d32"
TEAMS_COLOURS = {CRITICAL: "Attention", WARNING: "Warning", INFO: "Accent"}


# Message text ---------------------------------------------------------------


def headline(message: dict) -> str:
	if message["event"] == "resolved":
		return f"RESOLVED: {message['title']}"
	prefix = message["severity"].upper()
	if message["event"] in ("escalated", "outage") and message.get("escalation_label"):
		prefix = f"{prefix} - {message['escalation_label']}"
	elif message["event"] not in ("raised",):
		prefix = f"{prefix} - {message['event_label']}"
	return f"{prefix}: {message['title']}"


def subject(message: dict) -> str:
	return f"[{message['instance_label']}] {headline(message)}"[:240]


def facts(message: dict) -> list[tuple[str, str]]:
	rows = [
		("Instance", f"{message['instance_label']} ({message['environment']})"),
		("Event", message["event_label"]),
		("Severity", message["severity"]),
		("Category", message["category"]),
		("Source", message["source"]),
		("First seen", message["first_seen_display"]),
		("Last seen", message["last_seen_display"]),
		("Occurrences", str(message["occurrence_count"])),
	]
	if message.get("escalation_label"):
		rows.append(("Escalation", message["escalation_label"]))
	if message.get("is_outage"):
		rows.append(("Outage", "Yes - service is unavailable to users"))
	if message["event"] == "resolved" and message.get("duration_display"):
		rows.append(("Open for", message["duration_display"]))
	if message.get("acknowledged_by"):
		rows.append(("Acknowledged by", message["acknowledged_by"]))
	if message.get("reporting_due_display"):
		rows.append(
			(
				"Reporting",
				f"Possible CERT-In reportable incident: report to CERT-In and the institution by "
				f"{message['reporting_due_display']}. Declare a Security Incident from the alert to track it.",
			)
		)
	if message.get("alert"):
		rows.append(("Alert", message["alert"]))
	return rows


def short_text(message: dict) -> str:
	"""Text message body. Kept short because SMS gateways bill per 160-character part."""
	title = message["title"]
	if len(title) > 90:
		title = title[:87] + "..."
	if message["event"] == "resolved":
		body = f"[{message['instance_label']}] RESOLVED: {title}."
		if message.get("duration_display"):
			body += f" Open {message['duration_display']}."
	else:
		label = message["severity"].upper()
		if message["event"] != "raised":
			label = f"{label} {message['event_label']}"
		body = (
			f"[{message['instance_label']}] {label}: {title} ({message['category']})."
			f" Seen {message['occurrence_count']}x since {message['first_seen_display']}."
		)
		if message.get("reporting_due_display"):
			body += f" CERT-In report due by {message['reporting_due_display']}."
	if message.get("url"):
		body += f" {message['url']}"
	return body


def email_html(message: dict) -> str:
	colour = RESOLVED_COLOUR if message["event"] == "resolved" else SEVERITY_COLOURS.get(message["severity"])
	rows = "".join(
		f'<tr><td style="padding:6px 12px;color:#555;white-space:nowrap;vertical-align:top">{escape_html(k)}</td>'
		f'<td style="padding:6px 12px;color:#111">{escape_html(v)}</td></tr>'
		for k, v in facts(message)
	)
	description = ""
	if message.get("description"):
		description = (
			'<p style="margin:16px 0 0;white-space:pre-wrap;color:#222">'
			f"{escape_html(message['description'])}</p>"
		)
	button = ""
	if message.get("url"):
		button = (
			f'<p style="margin:20px 0 0"><a href="{escape_html(message["url"])}" '
			f'style="background:{colour};color:#fff;padding:10px 18px;border-radius:4px;'
			'text-decoration:none;display:inline-block">Open alert in Hostel360</a></p>'
		)
	return (
		'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;max-width:640px">'
		f'<div style="background:{colour};color:#fff;padding:14px 16px;border-radius:4px 4px 0 0">'
		f'<div style="font-size:12px;opacity:.9">{escape_html(message["instance_label"])} - '
		f"{escape_html(message['event_label'])}</div>"
		f'<div style="font-size:17px;font-weight:bold;margin-top:4px">{escape_html(headline(message))}</div>'
		"</div>"
		'<div style="border:1px solid #ddd;border-top:0;padding:12px 4px 16px;border-radius:0 0 4px 4px">'
		f'<table style="border-collapse:collapse;width:100%">{rows}</table>'
		f'<div style="padding:0 12px">{description}{button}</div>'
		"</div>"
		'<p style="font-size:12px;color:#777;margin-top:12px">HyFib Hostel360 operations alerting. '
		"Acknowledge the alert to stop repeat notifications.</p>"
		"</div>"
	)


# Chat payloads -------------------------------------------------------------


def _slack_escape(text: str) -> str:
	return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_payload(message: dict) -> dict:
	fields = [
		{"type": "mrkdwn", "text": f"*{_slack_escape(k)}*\n{_slack_escape(v)}"} for k, v in facts(message)
	][:10]
	blocks = [
		{"type": "header", "text": {"type": "plain_text", "text": headline(message)[:150], "emoji": False}},
		{"type": "section", "fields": fields},
	]
	if message.get("reporting_due_display"):
		# Slack shows at most ten fields; the reporting deadline must never be the one cut.
		blocks.append(
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"*CERT-In report due by {_slack_escape(message['reporting_due_display'])}*",
				},
			}
		)
	if message.get("description"):
		blocks.append(
			{"type": "section", "text": {"type": "mrkdwn", "text": _slack_escape(message["description"])[:2900]}}
		)
	if message.get("url"):
		blocks.append(
			{
				"type": "actions",
				"elements": [
					{
						"type": "button",
						"text": {"type": "plain_text", "text": "Open alert"},
						"url": message["url"],
					}
				],
			}
		)
	return {"text": subject(message), "blocks": blocks}


def teams_payload(message: dict) -> dict:
	"""Adaptive card, accepted by Teams Workflows webhooks and the older connector webhooks."""
	colour = "Good" if message["event"] == "resolved" else TEAMS_COLOURS.get(message["severity"], "Default")
	body = [
		{
			"type": "TextBlock",
			"text": headline(message),
			"weight": "Bolder",
			"size": "Medium",
			"color": colour,
			"wrap": True,
		},
		{
			"type": "TextBlock",
			"text": f"{message['instance_label']} - {message['event_label']}",
			"isSubtle": True,
			"spacing": "None",
			"wrap": True,
		},
		{"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in facts(message)]},
	]
	if message.get("description"):
		body.append({"type": "TextBlock", "text": message["description"][:3000], "wrap": True})
	card = {
		"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
		"type": "AdaptiveCard",
		"version": "1.4",
		"msteams": {"width": "Full"},
		"body": body,
	}
	if message.get("url"):
		card["actions"] = [{"type": "Action.OpenUrl", "title": "Open alert", "url": message["url"]}]
	return {
		"type": "message",
		"summary": subject(message),
		"attachments": [
			{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}
		],
	}


def google_chat_payload(message: dict) -> dict:
	widgets = [
		{"decoratedText": {"topLabel": escape_html(k), "text": escape_html(v), "wrapText": True}}
		for k, v in facts(message)
	]
	if message.get("description"):
		widgets.append({"textParagraph": {"text": escape_html(message["description"][:3000])}})
	if message.get("url"):
		widgets.append(
			{"buttonList": {"buttons": [{"text": "Open alert", "onClick": {"openLink": {"url": message["url"]}}}]}}
		)
	return {
		"text": subject(message),
		"cardsV2": [
			{
				"cardId": f"h360-{message.get('alert') or 'test'}",
				"card": {
					"header": {
						"title": headline(message)[:200],
						"subtitle": f"{message['instance_label']} - {message['event_label']}",
					},
					"sections": [{"widgets": widgets}],
				},
			}
		],
	}


def generic_payload(message: dict) -> dict:
	return {
		"event": f"alert.{message['event']}",
		"sent_at": message["sent_at"],
		"instance": message["instance_label"],
		"environment": message["environment"],
		"alert": {
			"name": message.get("alert"),
			"key": message.get("alert_key"),
			"title": message["title"],
			"severity": message["severity"],
			"category": message["category"],
			"source": message["source"],
			"status": message.get("status"),
			"is_outage": bool(message.get("is_outage")),
			"description": message.get("description") or "",
			"first_seen": message.get("first_seen"),
			"last_seen": message.get("last_seen"),
			"occurrence_count": message["occurrence_count"],
			"escalation_level": message.get("escalation_level") or 0,
			"url": message.get("url"),
		},
	}


PAYLOAD_BUILDERS = {
	CHANNEL_SLACK: slack_payload,
	CHANNEL_TEAMS: teams_payload,
	CHANNEL_GOOGLE_CHAT: google_chat_payload,
	CHANNEL_WEBHOOK: generic_payload,
}


# Senders --------------------------------------------------------------------


def send_email(recipients: list[str], message: dict) -> tuple[bool, str]:
	if not recipients:
		return True, "no recipients"
	critical = message["severity"] == CRITICAL and message["event"] != "resolved"
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject(message),
			message=email_html(message),
			reference_doctype="Ops Alert" if message.get("alert") else None,
			reference_name=message.get("alert"),
			add_unsubscribe_link=0,
			now=critical,
			x_priority=1 if critical else 3,
			send_priority=10 if critical else 1,
		)
	except Exception as exc:
		return False, _describe(exc)
	return True, "sent" if critical else "queued"


def send_text(channel: str, mobile: str, message: dict) -> tuple[bool, str]:
	try:
		from hostel360.hostel_communication.dispatch import send_direct
	except ImportError:
		return False, "messaging module unavailable"
	try:
		ok = send_direct(channel, mobile, subject(message), short_text(message))
	except Exception as exc:
		return False, _describe(exc)
	return bool(ok), "sent" if ok else "rejected"


def post_webhook(
	channel_type: str,
	url: str,
	message: dict,
	signing_secret: str | None = None,
	allow_http: bool = False,
) -> tuple[bool, str]:
	builder = PAYLOAD_BUILDERS.get(channel_type)
	if not builder:
		return False, "unsupported channel"
	headers = {}
	if channel_type == CHANNEL_WEBHOOK:
		headers["X-H360-Event"] = f"alert.{message['event']}"
	return post_json(url, builder(message), headers=headers, signing_secret=signing_secret, allow_http=allow_http)


def post_json(
	url: str,
	payload: dict,
	headers: dict | None = None,
	signing_secret: str | None = None,
	allow_http: bool = False,
) -> tuple[bool, str]:
	import requests

	if not url:
		return False, "no URL configured"
	if not (url.startswith("https://") or (allow_http and url.startswith("http://"))):
		return False, "URL must use https"

	body = json.dumps(payload, separators=(",", ":"), default=str).encode()
	request_headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
	if signing_secret:
		timestamp = str(int(time.time()))
		request_headers["X-H360-Timestamp"] = timestamp
		request_headers["X-H360-Signature"] = sign(signing_secret, timestamp, body)
	try:
		response = requests.post(
			url, data=body, headers=request_headers, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=False
		)
	except requests.RequestException as exc:
		return False, _describe(exc)
	if 200 <= response.status_code < 300:
		return True, f"HTTP {response.status_code}"
	return False, f"HTTP {response.status_code}"


def sign(secret: str, timestamp: str, body: bytes) -> str:
	"""Hex HMAC-SHA256 of ``<timestamp>.<body>``, the same scheme the ingest endpoint verifies."""
	return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _describe(exc: Exception) -> str:
	# Exception text from HTTP libraries can contain the webhook URL (and with it the
	# secret token), so only the exception class is recorded.
	return exc.__class__.__name__
