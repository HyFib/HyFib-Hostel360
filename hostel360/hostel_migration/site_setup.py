"""Non-interactive site setup for a new Hostel360 installation (``bench h360-setup``).

Used by the deployment playbooks so a fresh site never shows the setup wizard:

1. marks the setup wizard complete with Indian regional settings;
2. applies the Hostel360 security baseline, roles and module defaults (the same steps as
   ``after_install``);
3. fills the institution's basics in Hostel Settings and points the site's links at the
   public portal URL;
4. creates the first Hostel Administrator with a random password that is shown once.

Running it again is safe: settings are re-applied, an existing administrator keeps their
password unless a reset is asked for.
"""

import re
import secrets
import string
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import validate_email_address

from hostel360.setup.roles import ADMIN_ROLE

PASSWORD_LENGTH = 20
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "@#%+=?-"


class SiteSetupError(frappe.ValidationError):
	pass


def generate_password(length: int = PASSWORD_LENGTH) -> str:
	"""A random password that satisfies Frappe's strength check (letters, digits, symbols)."""
	while True:
		password = "".join(secrets.choice(PASSWORD_ALPHABET) for _i in range(length))
		if (
			any(c.islower() for c in password)
			and any(c.isupper() for c in password)
			and any(c.isdigit() for c in password)
			and any(c in "@#%+=?-" for c in password)
		):
			return password


def clean_portal_url(value: str) -> str:
	url = (value or "").strip().rstrip("/")
	parsed = urlparse(url)
	if parsed.scheme not in ("https", "http") or not parsed.netloc:
		raise SiteSetupError(_("Portal URL must be a full address such as https://hostel.example.ac.in"))
	local = parsed.hostname in ("localhost", "127.0.0.1") or (parsed.hostname or "").endswith(".localhost")
	if parsed.scheme != "https" and not local:
		raise SiteSetupError(_("Portal URL must use https:// outside local development."))
	if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
		raise SiteSetupError(_("Portal URL must be the site address only, without a path."))
	return f"{parsed.scheme}://{parsed.netloc}"


def complete_setup_wizard() -> None:
	"""What the setup wizard would record, without its browser steps."""
	from hostel360.setup.install import REGIONAL_SETTINGS, apply_settings

	apply_settings("System Settings", {**REGIONAL_SETTINGS, "language": "en"})
	if frappe.db.table_exists("Installed Application"):
		frappe.db.set_value(
			"Installed Application",
			{"app_name": ["in", frappe.get_installed_apps()]},
			"is_setup_complete",
			1,
		)
	frappe.db.set_single_value("System Settings", "setup_complete", 1)
	frappe.db.set_default("desktop:home_page", "workspace")

	try:
		from frappe.desk.page.setup_wizard import install_fixtures

		install_fixtures.install()
	except Exception:
		# Genders, salutations and dashboards are conveniences; their absence must not
		# stop a deployment.
		frappe.log_error(title="Hostel360 site setup: standard fixtures were not installed")


def update_hostel_settings(
	institution_name: str, short_name: str, portal_url: str, office_email: str | None
) -> None:
	settings = frappe.get_single("Hostel Settings")
	settings.institution_name = institution_name
	settings.institution_short_name = short_name
	settings.portal_url = portal_url
	if office_email:
		settings.office_email = office_email
	settings.flags.ignore_permissions = True
	settings.save()


def ensure_admin_user(
	email: str,
	first_name: str,
	last_name: str | None,
	system_manager: bool,
	reset_password: bool,
) -> tuple[str, str | None]:
	"""Create or update the first administrator. Returns (user, password or None)."""
	roles = [ADMIN_ROLE] + (["System Manager"] if system_manager else [])
	password = None
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
		if user.user_type != "System User":
			raise SiteSetupError(
				_("{0} is a portal account; choose another e-mail address for the administrator.").format(
					email
				)
			)
		user.enabled = 1
		user.add_roles(*[role for role in roles if role not in {r.role for r in user.roles}])
		if reset_password:
			password = generate_password()
			user.new_password = password
			user.flags.ignore_permissions = True
			user.save()
		return user.name, password

	password = generate_password()
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"user_type": "System User",
			"send_welcome_email": 0,
			"new_password": password,
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.no_welcome_mail = True
	user.flags.ignore_permissions = True
	user.insert()
	return user.name, password


def setup_site(
	institution_name: str,
	short_name: str,
	admin_email: str,
	portal_url: str,
	admin_name: str | None = None,
	office_email: str | None = None,
	system_manager: bool = True,
	reset_password: bool = False,
) -> dict:
	from frappe.installer import update_site_config

	from hostel360.setup.install import after_install

	institution_name = " ".join((institution_name or "").split())
	short_name = re.sub(r"\s+", "", short_name or "").upper()
	if len(institution_name) < 3:
		raise SiteSetupError(_("Enter the institution's full name."))
	if not 2 <= len(short_name) <= 20:
		raise SiteSetupError(_("The short name must be 2 to 20 characters, e.g. ACGCET."))
	admin_email = (admin_email or "").strip().lower()
	if not validate_email_address(admin_email):
		raise SiteSetupError(_("{0} is not a valid e-mail address.").format(admin_email))
	portal_url = clean_portal_url(portal_url)
	office_email = (office_email or "").strip().lower() or None
	if office_email and not validate_email_address(office_email):
		raise SiteSetupError(_("{0} is not a valid e-mail address.").format(office_email))

	first_name, _sep, last_name = (" ".join((admin_name or "").split()) or "Hostel Administrator").partition(
		" "
	)

	previous_mute = frappe.flags.mute_emails
	frappe.flags.mute_emails = True
	try:
		complete_setup_wizard()
		after_install()
		update_hostel_settings(institution_name, short_name, portal_url, office_email)
		user, password = ensure_admin_user(
			admin_email, first_name, last_name or None, system_manager, reset_password
		)
		frappe.db.commit()  # nosemgrep
	finally:
		frappe.flags.mute_emails = previous_mute

	# Links in e-mails and background jobs use host_name; it must be the public address.
	update_site_config("host_name", portal_url)
	frappe.clear_cache()
	return {
		"site": frappe.local.site,
		"institution_name": institution_name,
		"short_name": short_name,
		"portal_url": portal_url,
		"admin_user": user,
		"admin_password": password,
		"roles": [ADMIN_ROLE] + (["System Manager"] if system_manager else []),
	}
