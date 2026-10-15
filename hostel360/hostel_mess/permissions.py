"""Mess records follow the hostel scoping of the staff who work with them.

Deputy wardens, office staff and mess staff are limited to their hostels through User
Permissions on Hostel. Mess records carry a ``mess``, not a ``hostel``, so Frappe cannot
apply those permissions to them by itself. A user restricted to some hostels is therefore
restricted to the messes that serve at least one of those hostels; users without hostel
restrictions (Mess Manager, Warden, Administrator, Auditor) are not affected.

``mess_query_conditions`` and ``mess_has_permission`` are the permission hooks for the
DocTypes in ``MESS_SCOPED_DOCTYPES``. ``assert_mess_access`` is the same rule for
controllers and whitelisted helpers, so writes stay scoped even where a hook does not run.
"""

import frappe
from frappe import _

# DocType -> field holding the mess
MESS_SCOPED_DOCTYPES = {
	"Hostel Mess": "name",
	"Mess Menu": "mess",
	"Mess Stock Entry": "mess",
	"Mess Stock Ledger Entry": "mess",
	"Mess Indent": "mess",
	"Mess Expense": "mess",
	"Mess Bill Run": "mess",
	"Mess Bill": "mess",
	"Mess Extra Charge": "mess",
	"Mess Reduction Application": "mess",
}


def get_permitted_hostels(user: str | None = None, doctype: str | None = None) -> set[str] | None:
	"""Hostels the user is limited to by User Permissions; None when not limited."""
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	from frappe.permissions import get_user_permissions

	hostels = {
		row.get("doc")
		for row in get_user_permissions(user).get("Hostel") or []
		if not row.get("applicable_for") or (doctype and row.get("applicable_for") == doctype)
	}
	return hostels or None


def get_permitted_messes(user: str | None = None, doctype: str | None = None) -> set[str] | None:
	"""Messes serving the user's hostels; None when the user is not limited to hostels."""
	hostels = get_permitted_hostels(user, doctype)
	if hostels is None:
		return None
	return set(
		frappe.get_all(
			"Hostel Mess Hostel",
			filters={"parenttype": "Hostel Mess", "parentfield": "hostels", "hostel": ["in", list(hostels)]},
			pluck="parent",
			distinct=True,
		)
	)


def mess_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	field = MESS_SCOPED_DOCTYPES.get(doctype)
	if not field:
		return ""
	messes = get_permitted_messes(user, doctype)
	if messes is None:
		return ""
	if not messes:
		return "1 = 0"
	values = ", ".join(frappe.db.escape(m) for m in sorted(messes))
	return f"`tab{doctype}`.`{field}` in ({values})"


def mess_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Return False to deny; True leaves the decision to role permissions."""
	field = MESS_SCOPED_DOCTYPES.get(doc.doctype)
	if not field:
		return True
	messes = get_permitted_messes(user, doc.doctype)
	if messes is None:
		return True
	# Judge an existing record by what is stored, so a request cannot reach another mess's
	# record by sending a different mess with it, and by the request's value too, so a save
	# cannot move a record to a mess the user may not work with.
	if not doc.get("__islocal") and not (hasattr(doc, "is_new") and doc.is_new()) and doc.get("name"):
		stored = frappe.db.get_value(doc.doctype, doc.name, field)
		if stored and stored not in messes:
			return False
	value = doc.get(field)
	return not value or value in messes


def assert_mess_access(mess: str | None, user: str | None = None, doctype: str | None = None) -> None:
	"""Refuse when a hostel-restricted user works with a mess serving none of their hostels."""
	if not mess or frappe.flags.hostel360_migration or frappe.flags.in_install or frappe.flags.in_migrate:
		return
	messes = get_permitted_messes(user, doctype)
	if messes is None or mess in messes:
		return
	frappe.throw(
		_(
			"{0} does not serve any of the hostels you are assigned to, so you cannot work with its records."
		).format(frappe.bold(mess)),
		frappe.PermissionError,
		title=_("Not Permitted"),
	)
