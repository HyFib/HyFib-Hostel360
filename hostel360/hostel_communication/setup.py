"""Default messaging configuration, applied on install and after every migrate.

Missing records and unset settings are created. A template keeps a fingerprint of the
wording it was installed with: while the template still matches that fingerprint
(nobody has edited, disabled or registered it), a newer shipped wording replaces it.
As soon as an administrator changes anything, the template is never touched again.
"""

import frappe

from hostel360.hostel_communication.default_templates import get_default_templates

TEMPLATE_FIELDS = (
	"enabled",
	"subject",
	"body",
	"template_parameters",
	"dlt_template_id",
	"msg91_template_id",
	"whatsapp_template_name",
	"whatsapp_language",
)

SETTINGS_DEFAULTS = {
	"sms_provider": "MSG91",
	"whatsapp_api_version": "v21.0",
	"alert_whatsapp_language": "en",
	"email_enabled": 1,
	"otp_channels": "SMS",
	"otp_length": 6,
	"otp_expiry_seconds": 300,
	"otp_max_attempts": 5,
	"otp_resend_cooldown_seconds": 60,
	"otp_max_sends_per_hour": 5,
	"otp_max_sends_per_ip_per_hour": 20,
	"provider_timeout_seconds": 10,
	"failure_alert_threshold": 5,
	"sms_http_method": "POST",
	"sms_payload_format": "JSON",
	"sms_auth_param": "apikey",
	"sms_mobile_format": "91XXXXXXXXXX",
	"sms_param_mobile": "mobile",
	"sms_param_message": "message",
	"sms_param_sender": "sender",
	"sms_param_template_id": "template_id",
	"sms_param_entity_id": "entity_id",
}


def install_defaults() -> None:
	if not frappe.db.exists("DocType", "Messaging Settings"):
		return
	install_settings_defaults()
	install_templates()


def install_settings_defaults() -> None:
	"""Fill Messaging Settings fields that have never been saved; keep everything else."""
	singles = frappe.qb.DocType("Singles")
	stored = set(
		frappe.qb.from_(singles)
		.select(singles.field)
		.where(singles.doctype == "Messaging Settings")
		.run(pluck=True)
	)
	missing = {field: value for field, value in SETTINGS_DEFAULTS.items() if field not in stored}
	if not missing:
		return
	meta = frappe.get_meta("Messaging Settings")
	for field, value in missing.items():
		if meta.has_field(field):
			frappe.db.set_single_value("Messaging Settings", field, value)
	frappe.clear_document_cache("Messaging Settings", "Messaging Settings")


def install_templates() -> dict:
	"""Create missing default templates and refresh untouched ones. Returns counts."""
	existing = {
		row.name: row
		for row in frappe.get_all("Message Template", fields=["name", "standard_hash", *TEMPLATE_FIELDS])
	}
	created = updated = 0
	for spec in get_default_templates():
		name = f"{spec['event']}-{spec['channel']}"
		shipped = _template_from_spec(spec)
		shipped_hash = shipped.wording_fingerprint()
		row = existing.get(name)

		if not row:
			shipped.standard_hash = shipped_hash
			shipped.flags.from_install_defaults = True
			shipped.insert(ignore_permissions=True)
			created += 1
			continue

		if not row.standard_hash or row.standard_hash == shipped_hash:
			continue
		current = frappe.get_doc("Message Template", name)
		if current.wording_fingerprint() != row.standard_hash:
			# Edited by an administrator: leave it exactly as it is.
			continue
		for field in TEMPLATE_FIELDS:
			current.set(field, shipped.get(field))
		current.standard_hash = shipped_hash
		current.flags.from_install_defaults = True
		current.save(ignore_permissions=True)
		updated += 1
	return {"created": created, "updated": updated}


def _template_from_spec(spec: dict):
	doc = frappe.new_doc("Message Template")
	doc.event = spec["event"]
	doc.channel = spec["channel"]
	for field in TEMPLATE_FIELDS:
		if field in spec:
			doc.set(field, spec[field])
	doc.normalise()
	return doc
