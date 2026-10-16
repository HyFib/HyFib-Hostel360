"""Vocabulary shared by the operations module: severities, categories, sources, channels."""

ALERT_DOCTYPE = "Ops Alert"
SETTINGS_DOCTYPE = "Ops Alert Settings"
METRIC_DOCTYPE = "Ops Metric Sample"
BACKUP_DOCTYPE = "Ops Backup Record"
TICKET_DOCTYPE = "Support Ticket"
REPORT_DOCTYPE = "Monthly Performance Report"

INFO = "Info"
WARNING = "Warning"
CRITICAL = "Critical"
SEVERITIES = (INFO, WARNING, CRITICAL)
SEVERITY_RANK = {INFO: 1, WARNING: 2, CRITICAL: 3}

CATEGORIES = (
	"Availability",
	"Performance",
	"Capacity",
	"Backup",
	"Security",
	"Payments",
	"Messaging",
	"Jobs",
	"Errors",
	"SLA",
	"Other",
)

SOURCES = ("App", "Health Check", "Host Agent", "OCI Monitoring", "Uptime Probe", "Security", "Support")

STATUS_OPEN = "Open"
STATUS_ACKNOWLEDGED = "Acknowledged"
STATUS_RESOLVED = "Resolved"
STATUS_SUPPRESSED = "Suppressed"
# Statuses in which an alert key is "taken": a new occurrence updates the existing alert
# instead of opening a second one. Suppressed alerts keep counting but stay silent.
OPEN_STATUSES = (STATUS_OPEN, STATUS_ACKNOWLEDGED, STATUS_SUPPRESSED)
ACTIVE_STATUSES = (STATUS_OPEN, STATUS_ACKNOWLEDGED)

CHANNEL_EMAIL = "Email"
CHANNEL_SMS = "SMS"
CHANNEL_WHATSAPP = "WhatsApp"
CHANNEL_SLACK = "Slack Webhook"
CHANNEL_TEAMS = "Microsoft Teams Webhook"
CHANNEL_GOOGLE_CHAT = "Google Chat Webhook"
CHANNEL_WEBHOOK = "Generic Webhook"
WEBHOOK_CHANNELS = (CHANNEL_SLACK, CHANNEL_TEAMS, CHANNEL_GOOGLE_CHAT, CHANNEL_WEBHOOK)
MOBILE_CHANNELS = (CHANNEL_SMS, CHANNEL_WHATSAPP)

# Notification events, in the words recipients see.
EVENT_LABELS = {
	"raised": "New alert",
	"outage": "Outage",
	"escalated": "Escalated",
	"severity": "Severity increased",
	"reminder": "Reminder - not yet acknowledged",
	"reopened": "Re-opened",
	"sla_breach": "SLA breached",
	"acknowledged": "Acknowledged",
	"resolved": "Resolved",
	"test": "Test notification",
}

# CERT-In directions of 28 April 2022: reportable incidents are reported within six hours
# of being noticed. Security Incidents warn at four hours and flag a breach at six.
CERT_IN_REPORTING_HOURS = 6
CERT_IN_WARNING_HOURS = 4
SECURITY_INCIDENT_DOCTYPE = "Security Incident"

# A key that is resolved and raised again within this window re-opens the same alert
# (and stays quiet) instead of paging people for a flapping check.
REOPEN_WINDOW_MINUTES = 15

# Default escalation matrix from the HyFib support proposal (bid document 11, section 4).
DEFAULT_ESCALATION_LEVELS = (
	{
		"level": 1,
		"label": "HyFib helpdesk (24x7)",
		"after_minutes": 0,
		"min_severity": INFO,
		"notify_emails": "support@hyfib.com",
		"notify_mobiles": "9606060596",
		"mobile_min_severity": CRITICAL,
		"mobile_channel": "SMS",
		"outage_immediate": 0,
	},
	{
		"level": 2,
		"label": "HyFib technical lead",
		"after_minutes": 120,
		"min_severity": WARNING,
		"notify_emails": "",
		"notify_mobiles": "",
		"mobile_min_severity": CRITICAL,
		"mobile_channel": "SMS",
		"outage_immediate": 1,
	},
	{
		"level": 3,
		"label": "Director - Santhanakrishnan Manikandan",
		"after_minutes": 180,
		"min_severity": WARNING,
		"notify_emails": "manikandan.s@hyfib.com",
		"notify_mobiles": "9741380530",
		"mobile_min_severity": CRITICAL,
		"mobile_channel": "SMS",
		"outage_immediate": 1,
	},
)


def severity_rank(severity: str | None) -> int:
	return SEVERITY_RANK.get(severity or "", SEVERITY_RANK[WARNING])
