"""Sharing rules for Hostel360 records.

Frappe lets anyone with share permission share a document with "everyone" or with any
single user. "Everyone" means every enabled account on the site, student and parent
portal accounts included, and a share is honoured by the generic ``/api/resource`` and
``frappe.client`` endpoints. Students and parents reach their own records only through
the portal API, which checks that the record is theirs; a share would bypass that check
and hand one resident's details, fees or grievances to another.

So, for Hostel360 documents and for the core records that carry personal data (files,
users, contacts, addresses, e-mails):

* sharing with everyone is refused;
* sharing with an account that has no desk access (portal users, website visitors) is
  refused.

Sharing with a named desk colleague stays possible: it is a deliberate act by someone who
already holds share permission, and it is recorded on the document's timeline. So does the
share Frappe makes of every User record with that user, so people can edit their own
account settings.

``validate_doc_share`` runs as the DocShare ``validate`` hook; ``remove_unsafe_shares``
clears shares made before the rule existed and is safe to run after every migrate.
"""

import frappe
from frappe import _
from frappe.utils import cint

from hostel360.security.audit import security_log

APP_NAME = "hostel360"
# Core DocTypes that hold students' and parents' personal data or uploaded documents.
PERSONAL_CORE_DOCTYPES = frozenset({"File", "User", "Contact", "Address", "Communication"})
DESK_USER_TYPE = "System User"


class ShareNotAllowed(frappe.PermissionError):
	"""A share that would expose a Hostel360 record outside the desk roles."""


def is_protected_doctype(doctype: str | None) -> bool:
	"""Hostel360 DocTypes and the core DocTypes listed in ``PERSONAL_CORE_DOCTYPES``."""
	if not doctype:
		return False
	if doctype in PERSONAL_CORE_DOCTYPES:
		return True
	module = frappe.db.get_value("DocType", doctype, "module", cache=True)
	return bool(module) and module in _app_modules()


def _app_modules() -> frozenset[str]:
	modules = getattr(frappe.local, "hostel360_modules", None)
	if modules is None:
		modules = frozenset(frappe.get_module_list(APP_NAME))
		frappe.local.hostel360_modules = modules
	return modules


def is_desk_account(user: str | None) -> bool:
	if not user:
		return False
	return frappe.db.get_value("User", user, "user_type", cache=True) == DESK_USER_TYPE


def is_self_share(share_doctype: str | None, share_name: str | None, user: str | None) -> bool:
	"""Frappe shares each User record with that user (``User.share_with_self``)."""
	return share_doctype == "User" and bool(user) and share_name == user


def validate_doc_share(doc, method=None) -> None:
	"""DocShare ``validate``: refuse shares that would reach beyond desk colleagues."""
	if not is_protected_doctype(doc.share_doctype):
		return
	if not cint(doc.everyone) and is_self_share(doc.share_doctype, doc.share_name, doc.user):
		return
	if cint(doc.everyone):
		_refuse(
			doc,
			"share_with_everyone",
			_(
				"{0} records cannot be shared with everyone: that would include every student and "
				"parent portal account. Share the document with the colleagues who need it instead."
			).format(_(doc.share_doctype)),
		)
	if doc.user and not is_desk_account(doc.user):
		_refuse(
			doc,
			"share_with_portal_account",
			_(
				"{0} records cannot be shared with {1}. Students and parents see their own records "
				"through the hostel portal only."
			).format(_(doc.share_doctype), frappe.bold(doc.user)),
		)


def _refuse(doc, reason: str, message: str) -> None:
	security_log(
		"share_refused",
		level="warning",
		reason=reason,
		doctype=doc.share_doctype,
		document=doc.share_name,
		by_hash=_hash(frappe.session.user),
	)
	frappe.throw(message, ShareNotAllowed, title=_("Sharing Not Allowed"))


def remove_unsafe_shares() -> int:
	"""Delete existing shares that the rule above would refuse. Returns how many went.

	Idempotent; meant to run after migrate so shares made before the rule existed, or
	while it was not wired, do not linger.
	"""
	share = frappe.qb.DocType("DocShare")
	user = frappe.qb.DocType("User")
	rows = (
		frappe.qb.from_(share)
		.left_join(user)
		.on(user.name == share.user)
		.select(share.name, share.share_doctype, share.share_name, share.user, share.everyone)
		.where((share.everyone == 1) | (user.user_type.isnull()) | (user.user_type != DESK_USER_TYPE))
	).run(as_dict=True)

	removed = [
		row.name
		for row in rows
		if is_protected_doctype(row.share_doctype)
		and (cint(row.everyone) or not is_self_share(row.share_doctype, row.share_name, row.user))
	]
	if not removed:
		return 0
	frappe.db.delete("DocShare", {"name": ("in", removed)})
	security_log("unsafe_shares_removed", level="warning", count=len(removed))
	return len(removed)


def _hash(value: str | None) -> str | None:
	if not value:
		return None
	import hashlib

	return hashlib.sha256(value.encode()).hexdigest()[:12]
