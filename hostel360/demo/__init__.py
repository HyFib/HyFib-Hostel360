"""Demonstration mode: an open, public Hostel360 site filled with fictitious data.

A site is a demonstration site while its ``site_config.json`` carries
``"hostel360_demo_mode": 1``. Only ``bench --site SITE h360-demo-site`` writes that key;
nothing in the desk or the portal can switch the mode on or off, and every safeguard below
reads the site configuration, not a setting a signed-in visitor could change.

While the mode is on:

* every page says so (portal, desk and the certificate check) and links to ``/demo``;
* SMS, WhatsApp and e-mail providers never send (Message Log rows are written as Skipped
  with the reason "Demonstration site"), operations alerts never leave the server, the
  dead-man heartbeat is not called and payment gateways are never asked anything;
* the published demo accounts (``hostel360_demo_users``) cannot be taken over: their
  password, e-mail address, roles and sign-in restrictions stay as the command set them;
* portal sign-in accepts the published demo code for numbers that belong to records on
  the site, with the usual rate limits.

With the key absent (every production site) none of this code changes anything.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr

DEMO_MODE_KEY = "hostel360_demo_mode"
PASSWORD_KEY = "hostel360_demo_password"  # noqa: S105 - the site_config key, not a password
OTP_KEY = "hostel360_demo_otp"
USERS_KEY = "hostel360_demo_users"
ACCOUNTS_KEY = "hostel360_demo_accounts"
# Every key the demo command writes into site_config.json (re-applied after a reset).
CONFIG_KEYS = (DEMO_MODE_KEY, PASSWORD_KEY, OTP_KEY, USERS_KEY, ACCOUNTS_KEY, "mute_emails")

DEFAULT_OTP = "246810"
# Recorded on every message that was not sent because the site is a demonstration.
DEMO_REASON = "Demonstration site"
INFO_ROUTE = "/demo"

PORTAL_KINDS = ("student", "parent")
ACCOUNT_FIELDS = ("user", "kind", "role", "name", "mobile", "hostel", "note")


def _conf():
	try:
		return frappe.local.conf or {}
	except AttributeError:
		# No site initialised (a bench command before frappe.init): never a demo.
		return {}


def is_demo_site() -> bool:
	"""True while site_config.json switches demonstration mode on."""
	return bool(cint(_conf().get(DEMO_MODE_KEY)))


def demo_otp() -> str:
	"""The published portal sign-in code (four to eight digits; 246810 unless configured)."""
	value = cstr(_conf().get(OTP_KEY)).strip()
	return value if value.isdigit() and 4 <= len(value) <= 8 else DEFAULT_OTP


def demo_password() -> str | None:
	"""The shared password of the published desk accounts, None when none was set."""
	return cstr(_conf().get(PASSWORD_KEY)).strip() or None


def demo_users() -> frozenset[str]:
	"""Accounts locked against changes while the site is a demonstration."""
	users = _conf().get(USERS_KEY) or []
	if isinstance(users, str):
		users = [users]
	if not isinstance(users, list | tuple):
		return frozenset()
	return frozenset(cstr(user).strip() for user in users if cstr(user).strip())


def is_demo_user(user: str | None) -> bool:
	return bool(user) and is_demo_site() and user in demo_users()


def demo_accounts() -> list[dict]:
	"""The sign-ins published on /demo, as the demo command recorded them."""
	rows = _conf().get(ACCOUNTS_KEY) or []
	if not isinstance(rows, list):
		return []
	accounts = []
	for row in rows:
		if not isinstance(row, dict) or not row.get("kind"):
			continue
		account = {field: cstr(row.get(field)).strip() or None for field in ACCOUNT_FIELDS}
		wards = row.get("wards")
		account["wards"] = (
			[cstr(w).strip() for w in wards if cstr(w).strip()] if isinstance(wards, list) else []
		)
		accounts.append(account)
	return accounts


def portal_accounts() -> list[dict]:
	"""Demo students and parents: what the portal's sign-in helper lists."""
	# ``hostel``, ``story`` and ``wards`` let the portal word the role in the visitor's language.
	return [
		{
			"kind": row["kind"],
			"name": row["name"],
			"role": row["role"],
			"mobile": row["mobile"],
			"hostel": row["hostel"],
			"story": row["note"],
			"wards": row["wards"],
		}
		for row in demo_accounts()
		if row["kind"] in PORTAL_KINDS and row.get("mobile")
	]


def staff_accounts() -> list[dict]:
	return [row for row in demo_accounts() if row["kind"] == "staff" and row.get("user")]


def portal_boot() -> dict | None:
	"""What the portal page needs to show the banner and the sign-in helper (None: not a demo)."""
	if not is_demo_site():
		return None
	return {"info_url": INFO_ROUTE, "otp": demo_otp(), "accounts": portal_accounts()}


def desk_boot() -> dict | None:
	if not is_demo_site():
		return None
	return {"info_url": INFO_ROUTE}


def payment_off_message() -> str:
	return _("Online payment is switched off on this demonstration site; the office records payments.")


def banner_text() -> dict:
	"""The banner wording in both portal languages, for the server-rendered pages."""
	return {
		"en": "Demonstration site — all data is fictitious",
		"ta": "இது செயல்விளக்கத் தளம் — இங்குள்ள தகவல்கள் அனைத்தும் கற்பனையானவை",
		"link_en": "About this demo",
		"link_ta": "விவரம்",
	}
