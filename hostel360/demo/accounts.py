"""The published demo accounts cannot be taken over.

Everyone who opens the demonstration site gets the same sign-ins. One visitor changing an
account's password, e-mail address or roles, disabling it, deleting it or creating API keys
for it would lock everybody else out (or keep a way in after the nightly reset), so these
changes are refused for the accounts listed in ``hostel360_demo_users`` while the site is a
demonstration. Their Hostel Staff records are protected the same way.

The demo command itself changes these accounts with ``frappe.flags.hostel360_demo_setup``
set. Nothing else lifts the lock: to change a demo account, run the command again.
"""

import frappe
from frappe import _

from hostel360.demo import is_demo_site, is_demo_user

SETUP_FLAG = "hostel360_demo_setup"

# User fields that decide who can sign in to the account and with what.
LOCKED_USER_FIELDS = (
	"email",
	"username",
	"mobile_no",
	"enabled",
	"user_type",
	"role_profile_name",
	"module_profile",
	"simultaneous_sessions",
	"login_after",
	"login_before",
	"restrict_ip",
	"bypass_restrict_ip_check_if_2fa_enabled",
	"api_key",
	"api_secret",
)
# Child tables compared by the values that matter.
LOCKED_USER_TABLES = {
	"roles": ("role",),
	"role_profiles": ("role_profile",),
	"block_modules": ("module",),
	"social_logins": ("provider", "userid"),
}
LOCKED_STAFF_FIELDS = ("designation", "user", "email", "is_active", "create_user")
# Roles every account carries implicitly.
AUTOMATIC_ROLES = frozenset({"All", "Guest", "Desk User"})


def in_setup() -> bool:
	return bool(frappe.flags.get(SETUP_FLAG))


def refuse(user: str, what: str) -> None:
	frappe.throw(
		_(
			"{0} is a demonstration account shared by everyone trying this site, so its {1} cannot be "
			"changed here. The demonstration data, accounts included, is restored every night."
		).format(frappe.bold(user), what),
		frappe.PermissionError,
		title=_("Demonstration Account"),
	)


# --------------------------------------------------------------------------- User


def validate_demo_user(doc) -> None:
	"""User ``validate``: keep a demo account's sign-in details as the demo command set them."""
	if in_setup() or not is_demo_user(doc.name):
		return
	# Frappe's own User.validate runs first and moves a new password out of the field into a
	# private attribute until on_update stores it.
	if doc.get("new_password") or getattr(doc, "_User__new_password", None):
		refuse(doc.name, _("password"))

	before = doc.get_doc_before_save()
	if before is None:
		return
	for fieldname in LOCKED_USER_FIELDS:
		if doc.meta.has_field(fieldname) and doc.get(fieldname) != before.get(fieldname):
			refuse(doc.name, _(doc.meta.get_label(fieldname)).lower())
	for table, keys in LOCKED_USER_TABLES.items():
		if not doc.meta.has_field(table):
			continue
		if _rows(doc, table, keys) != _rows(before, table, keys):
			refuse(
				doc.name, _("roles") if table in ("roles", "role_profiles") else _(doc.meta.get_label(table))
			)


def _rows(doc, table: str, keys: tuple[str, ...]) -> set:
	values = set()
	for row in doc.get(table) or []:
		value = tuple(row.get(key) for key in keys)
		if table == "roles" and value[0] in AUTOMATIC_ROLES:
			continue
		values.add(value)
	return values


def protect_user_on_trash(doc, method=None) -> None:
	"""User ``on_trash``: demo accounts cannot be deleted."""
	if not in_setup() and is_demo_user(doc.name):
		refuse(doc.name, _("account"))


def protect_user_rename(doc, method=None, old=None, new=None, merge=False) -> None:
	"""User ``before_rename``: the login ID of a demo account stays as published."""
	if not in_setup() and is_demo_user(old or doc.name):
		refuse(old or doc.name, _("login ID"))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def update_password(
	new_password: str, logout_all_sessions: int = 0, key: str | None = None, old_password: str | None = None
):
	"""Frappe's password change (reset link or old password), refused for demo accounts."""
	from frappe.core.doctype.user import user as user_module

	if is_demo_site() and not in_setup():
		target = _password_change_target(key)
		if is_demo_user(target):
			refuse(target, _("password"))
	return user_module.update_password(
		new_password, logout_all_sessions=logout_all_sessions, key=key, old_password=old_password
	)


def _password_change_target(key: str | None) -> str | None:
	"""Whose password the request would change: the reset link's owner, else the caller."""
	if key:
		from frappe.utils import sha256_hash

		# Frappe keeps only the hash of the key it e-mailed.
		return frappe.db.get_value("User", {"reset_password_key": sha256_hash(key)}, "name")
	return frappe.session.user


@frappe.whitelist()
def generate_keys(user: str):
	"""Frappe's API key generation, refused for demo accounts."""
	from frappe.core.doctype.user import user as user_module

	if not in_setup() and is_demo_user(user):
		refuse(user, _("API keys"))
	return user_module.generate_keys(user)


# --------------------------------------------------------------------------- Hostel Staff


def validate_demo_staff(doc) -> None:
	"""Hostel Staff ``validate``: a demo account's staff record keeps its role and hostels."""
	if in_setup() or doc.is_new():
		return
	before = doc.get_doc_before_save()
	user = (before.get("user") if before else None) or doc.get("user")
	if not is_demo_user(user):
		return
	for fieldname in LOCKED_STAFF_FIELDS:
		if before and doc.get(fieldname) != before.get(fieldname):
			refuse(user, _(doc.meta.get_label(fieldname)).lower())
	if before and _assignments(doc) != _assignments(before):
		refuse(user, _("hostel assignments"))


def _assignments(doc) -> set:
	return {(row.get("hostel"), row.get("block") or None) for row in doc.get("assignments") or []}


def protect_demo_staff_delete(doc) -> None:
	"""Hostel Staff ``on_trash``: a demo account's staff record cannot be deleted."""
	if not in_setup() and is_demo_user(doc.get("user")):
		refuse(doc.user, _("staff record"))
