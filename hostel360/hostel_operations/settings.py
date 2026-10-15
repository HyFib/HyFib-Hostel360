"""Access to Ops Alert Settings and the small parsers its free-text fields need."""

import re

import frappe
from frappe.utils import cint, flt, validate_email_address

from hostel360.hostel_operations.constants import SETTINGS_DOCTYPE, severity_rank

# Fallbacks used when a threshold has been cleared on the settings form; the numbers
# match the defaults written by install_defaults().
THRESHOLD_DEFAULTS = {
	"disk_warning_percent": 80,
	"disk_critical_percent": 90,
	"queue_backlog_threshold": 500,
	"failed_job_threshold": 10,
	"db_latency_warning_ms": 500,
	"error_spike_threshold": 25,
	"failed_login_threshold": 30,
	"scheduler_stale_minutes": 10,
	"payment_failure_threshold": 10,
	"pending_payment_minutes": 30,
	"message_failure_percent": 20,
	"backup_max_age_hours": 26,
	"restore_test_max_age_days": 35,
	"ssl_expiry_warning_days": 21,
	"agent_silence_minutes": 10,
	"critical_renotify_minutes": 60,
	"ticket_response_hours": 1,
	"ticket_resolution_hours": 4,
}

_SPLIT_RE = re.compile(r"[\s,;]+")


def get_settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def threshold(settings, fieldname: str) -> float:
	"""A numeric setting, falling back to the shipped default when left empty or zero."""
	value = flt(settings.get(fieldname))
	return value if value > 0 else THRESHOLD_DEFAULTS[fieldname]


def split_list(value: str | None) -> list[str]:
	"""Split a comma/newline separated field into clean, de-duplicated entries."""
	seen = []
	for part in _SPLIT_RE.split(value or ""):
		part = part.strip()
		if part and part not in seen:
			seen.append(part)
	return seen


def parse_emails(value: str | None, throw: bool = False) -> list[str]:
	emails = []
	for part in split_list(value):
		email = validate_email_address(part, throw=throw)
		if email and email.lower() not in emails:
			emails.append(email.lower())
	return emails


def parse_mobiles(value: str | None, throw: bool = False) -> list[str]:
	from hostel360.utils.validation import normalize_mobile

	mobiles = []
	# Mobile numbers are often written with spaces ("+91 96060 60596"), so split on
	# commas, semicolons and new lines only.
	for part in re.split(r"[,;\n]+", value or ""):
		part = part.strip()
		if not part:
			continue
		try:
			mobile = normalize_mobile(part)
		except frappe.ValidationError:
			if throw:
				raise
			continue
		if mobile and mobile not in mobiles:
			mobiles.append(mobile)
	return mobiles


def get_levels(settings) -> list:
	"""Escalation levels sorted by level number."""
	return sorted(settings.get("escalation_levels") or [], key=lambda row: cint(row.level))


def level_accepts(level_row, severity: str) -> bool:
	return severity_rank(severity) >= severity_rank(level_row.min_severity or "Warning")


def parse_levels(value: str | None) -> set[int]:
	return {cint(part) for part in split_list(value) if cint(part) > 0}


def format_levels(levels) -> str:
	return ",".join(str(level) for level in sorted(set(levels)))


def level_one_emails(settings) -> list[str]:
	levels = get_levels(settings)
	if not levels:
		return []
	return parse_emails(levels[0].notify_emails)
