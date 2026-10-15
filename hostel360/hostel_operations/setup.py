"""Default configuration for HyFib operations alerting. Safe to run on every migrate."""

import secrets

import frappe

from hostel360.hostel_operations.constants import DEFAULT_ESCALATION_LEVELS, SETTINGS_DOCTYPE
from hostel360.hostel_operations.settings import THRESHOLD_DEFAULTS


def install_defaults() -> None:
	# Single DocTypes have no table of their own; check the DocType record instead.
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return

	settings = frappe.get_doc(SETTINGS_DOCTYPE)
	changed = False

	if not settings.get("instance_label"):
		settings.instance_label = "ACGCET Hostel360"
		changed = True

	for fieldname, value in THRESHOLD_DEFAULTS.items():
		if settings.meta.has_field(fieldname) and not settings.get(fieldname):
			settings.set(fieldname, value)
			changed = True

	if not settings.get("escalation_levels"):
		for row in DEFAULT_ESCALATION_LEVELS:
			settings.append("escalation_levels", dict(row))
		changed = True

	# Secrets are created once and never rotated here; rotating them would break the
	# host agent and the OCI subscription until they are reconfigured.
	if not settings.get_password("ingest_secret", raise_exception=False):
		settings.ingest_secret = secrets.token_hex(32)
		changed = True
	if not settings.get_password("oci_notification_token", raise_exception=False):
		settings.oci_notification_token = secrets.token_urlsafe(32)
		changed = True

	if not frappe.db.get_single_value(SETTINGS_DOCTYPE, "defaults_installed"):
		# First install only: afterwards these are the operators' decisions.
		settings.enabled = 1
		settings.environment = "Development" if frappe.conf.developer_mode else "Production"
		settings.defaults_installed = 1
		changed = True

	if changed:
		settings.flags.ignore_permissions = True
		settings.flags.ignore_mandatory = True
		settings.save()


def rotate_secret(fieldname: str) -> str:
	"""Replace the ingest secret or the OCI token; returns the new value once."""
	if fieldname not in ("ingest_secret", "oci_notification_token"):
		frappe.throw(frappe._("Only the ingest secret and the OCI notification token can be rotated."))
	value = secrets.token_hex(32) if fieldname == "ingest_secret" else secrets.token_urlsafe(32)
	settings = frappe.get_doc(SETTINGS_DOCTYPE)
	settings.set(fieldname, value)
	settings.flags.ignore_permissions = True
	settings.save()
	return value
