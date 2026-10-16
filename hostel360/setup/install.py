"""Installation and migration hooks.

``after_install`` applies the hardened baseline configuration once. ``after_migrate``
re-applies everything that must always hold (roles, default templates, settings
records) and is safe to run on every deploy.
"""

import frappe

from hostel360.setup.roles import ROLES, TWO_FACTOR_CORE_ROLES

# Each module exposes an idempotent ``install_defaults()``.
MODULE_SETUP_HOOKS = (
	"hostel360.hostel_setup.setup.install_defaults",
	"hostel360.hostel_people.setup.install_defaults",
	"hostel360.hostel_fees.setup.install_defaults",
	"hostel360.hostel_mess.setup.install_defaults",
	"hostel360.student_services.setup.install_defaults",
	"hostel360.hostel_communication.setup.install_defaults",
	"hostel360.hostel_operations.setup.install_defaults",
	"hostel360.hostel_migration.setup.install_defaults",
)

# Regional defaults for an Indian institution.
REGIONAL_SETTINGS = {
	"country": "India",
	"time_zone": "Asia/Kolkata",
	"currency": "INR",
	"date_format": "dd-mm-yyyy",
	"time_format": "HH:mm",
	"number_format": "#,##,###.##",
	"float_precision": "2",
	"currency_precision": "2",
	"first_day_of_the_week": "Monday",
}

# Security baseline (tender T&C 28, technical requirement 9).
SECURITY_SETTINGS = {
	"enable_two_factor_auth": 1,
	"two_factor_method": "OTP App",
	"otp_issuer_name": "HyFib Hostel360",
	"lifespan_qrcode_image": 240,
	"enable_password_policy": 1,
	"minimum_password_score": "3",
	"allow_consecutive_login_attempts": 5,
	"allow_login_after_fail": 600,
	"session_expiry": "08:00",
	"force_user_to_reset_password": 180,
	"logout_on_password_reset": 1,
	"password_reset_limit": 3,
	"allow_guests_to_upload_files": 0,
	"only_allow_system_managers_to_upload_public_files": 1,
	"strip_exif_metadata_from_uploaded_images": 1,
	"max_file_size": 10,
	# Frappe compares the upper-cased extension against these lines.
	"allowed_file_extensions": "PDF\nJPG\nJPEG\nPNG\nWEBP\nXLSX\nXLS\nCSV\nJSON\nMD\nTXT\nZIP",
	"allow_error_traceback": 0,
	"encrypt_backup": 1,
	"enable_telemetry": 0,
	"disable_product_suggestion": 1,
	"disable_system_update_notification": 1,
	"login_with_email_link": 0,
	"enable_onboarding": 0,
	"max_signups_allowed_per_hour": 0,
	"show_external_link_warning": "Ask",
}

# CERT-In directions (28 April 2022): ICT system logs must be retained for at least 180 days.
# Frappe's own defaults are shorter, so these are raised (never lowered) on every migrate.
MIN_LOG_RETENTION_DAYS = {
	"Activity Log": 365,
	"Access Log": 365,
	"API Request Log": 180,
	"Error Log": 180,
	"Route History": 180,
	"Integration Request": 180,
	"Webhook Request Log": 180,
}

WEBSITE_SETTINGS = {
	"disable_signup": 1,
	"home_page": "hostel",
	"app_name": "Hostel360",
	"hide_footer_signup": 1,
}


def after_install():
	apply_settings("System Settings", {**REGIONAL_SETTINGS, **SECURITY_SETTINGS})
	apply_settings("Website Settings", WEBSITE_SETTINGS)
	sync_roles()
	enforce_log_retention()
	enable_languages()
	remove_unsafe_shares()
	run_module_setup()
	frappe.db.commit()  # nosemgrep


def after_migrate():
	sync_roles()
	enforce_log_retention()
	enable_languages()
	remove_unsafe_shares()
	run_module_setup()


def before_tests():
	"""Prepare a fresh test site: skip the setup wizard and apply defaults."""
	frappe.clear_cache()
	if not frappe.db.get_single_value("System Settings", "setup_complete"):
		apply_settings("System Settings", {**REGIONAL_SETTINGS, "setup_complete": 1})
		frappe.db.set_default("desktop:home_page", "workspace")
	sync_roles()
	run_module_setup()
	frappe.db.commit()  # nosemgrep


def apply_settings(doctype: str, values: dict) -> None:
	"""Set fields on a single DocType, skipping any field this Frappe version lacks."""
	meta = frappe.get_meta(doctype)
	for fieldname, value in values.items():
		if meta.has_field(fieldname):
			frappe.db.set_single_value(doctype, fieldname, value)
	frappe.clear_document_cache(doctype, doctype)


def sync_roles() -> None:
	for role_name, desk_access, two_factor_auth, _description in ROLES:
		if frappe.db.exists("Role", role_name):
			frappe.db.set_value(
				"Role",
				role_name,
				{"desk_access": desk_access, "two_factor_auth": two_factor_auth, "disabled": 0},
				update_modified=False,
			)
			continue
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role_name,
				"desk_access": desk_access,
				"two_factor_auth": two_factor_auth,
				"is_custom": 0,
			}
		).insert(ignore_permissions=True)

	for role_name in TWO_FACTOR_CORE_ROLES:
		if frappe.db.exists("Role", role_name):
			frappe.db.set_value("Role", role_name, "two_factor_auth", 1, update_modified=False)


def remove_unsafe_shares() -> None:
	"""Shares created before the DocShare rule existed are removed (idempotent)."""
	from hostel360.security.sharing import remove_unsafe_shares as _remove

	_remove()


def enable_languages() -> None:
	"""The portal and desk are offered in English and Tamil."""
	for code in ("en", "ta"):
		if frappe.db.exists("Language", code):
			frappe.db.set_value("Language", code, "enabled", 1, update_modified=False)


def enforce_log_retention() -> None:
	from frappe.core.doctype.log_settings.log_settings import _supports_log_clearing
	from frappe.utils import cint

	settings = frappe.get_single("Log Settings")
	changed = False
	for doctype, days in MIN_LOG_RETENTION_DAYS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		row = next((r for r in settings.logs_to_clear if r.ref_doctype == doctype), None)
		if row is None:
			if _supports_log_clearing(doctype):
				settings.append("logs_to_clear", {"ref_doctype": doctype, "days": days})
				changed = True
		elif cint(row.days) < days:
			row.days = days
			changed = True
	if changed:
		settings.flags.ignore_permissions = True
		settings.save()


def run_module_setup() -> None:
	import importlib.util

	for method in MODULE_SETUP_HOOKS:
		module_path = method.rpartition(".")[0]
		if importlib.util.find_spec(module_path) is None:
			continue
		frappe.get_attr(method)()
