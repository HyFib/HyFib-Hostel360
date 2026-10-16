"""Mess records follow the hostel scoping of the staff who work with them.

Deputy wardens, office staff and mess staff are limited to their hostels through User
Permissions on Hostel. Most mess records carry a ``mess``, not a ``hostel``, so Frappe cannot
apply those permissions to them by itself. A user restricted to some hostels is therefore
restricted to the messes that serve at least one of those hostels; users without hostel
restrictions (Mess Manager, Warden, Administrator, Auditor) are not affected.

Bills, extra charges and reduction applications also record the student's hostel. The
messes serving a hostel can change (a mess is split, or a hostel moves to another kitchen),
and the hostel's staff must keep seeing their own students' earlier records afterwards. For
these DocTypes an existing record is also visible when its stored hostel is one of the
user's hostels. New records, and edits that change the mess, still need a mess that serves
one of the user's hostels, so nobody can file or move records into a mess they do not work
with.

``mess_query_conditions`` and ``mess_has_permission`` are the permission hooks for the
DocTypes in ``MESS_SCOPED_DOCTYPES``. ``assert_mess_access`` and ``assert_doc_mess_access``
are the same rule for controllers and whitelisted helpers, so writes stay scoped even where a
hook does not run.
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

# DocType -> field holding the student's hostel, for the records that belong to a student.
# Frappe's own User Permission filter on this Link still applies, so the hostel rule below
# can only ever reach records of the user's own hostels.
HOSTEL_SCOPED_DOCTYPES = {
	"Mess Bill": "hostel",
	"Mess Extra Charge": "hostel",
	"Mess Reduction Application": "hostel",
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


def get_messes_serving(hostels) -> set[str]:
	"""Messes that currently serve any of ``hostels``."""
	if not hostels:
		return set()
	return set(
		frappe.get_all(
			"Hostel Mess Hostel",
			filters={"parenttype": "Hostel Mess", "parentfield": "hostels", "hostel": ["in", list(hostels)]},
			pluck="parent",
			distinct=True,
		)
	)


def get_permitted_messes(user: str | None = None, doctype: str | None = None) -> set[str] | None:
	"""Messes serving the user's hostels; None when the user is not limited to hostels."""
	hostels = get_permitted_hostels(user, doctype)
	if hostels is None:
		return None
	return get_messes_serving(hostels)


def _in_clause(doctype: str, field: str, values) -> str:
	escaped = ", ".join(frappe.db.escape(v) for v in sorted(values))
	return f"`tab{doctype}`.`{field}` in ({escaped})"


def mess_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	field = MESS_SCOPED_DOCTYPES.get(doctype)
	if not field:
		return ""
	hostels = get_permitted_hostels(user, doctype)
	if hostels is None:
		return ""

	conditions = []
	messes = get_messes_serving(hostels)
	if messes:
		conditions.append(_in_clause(doctype, field, messes))
	hostel_field = HOSTEL_SCOPED_DOCTYPES.get(doctype)
	if hostel_field:
		conditions.append(_in_clause(doctype, hostel_field, hostels))

	if not conditions:
		return "1 = 0"
	if len(conditions) == 1:
		return conditions[0]
	return "({})".format(" or ".join(conditions))


def _stored_values(doc, fields: list[str]) -> frappe._dict | None:
	"""The record as saved, or None for a document that is not in the database yet."""
	if doc.get("__islocal") or (hasattr(doc, "is_new") and doc.is_new()) or not doc.get("name"):
		return None
	return frappe.db.get_value(doc.doctype, doc.name, fields, as_dict=True)


def mess_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Return False to deny; True leaves the decision to role permissions."""
	field = MESS_SCOPED_DOCTYPES.get(doc.doctype)
	if not field:
		return True
	hostels = get_permitted_hostels(user, doc.doctype)
	if hostels is None:
		return True
	messes = get_messes_serving(hostels)
	hostel_field = HOSTEL_SCOPED_DOCTYPES.get(doc.doctype)
	value = doc.get(field)

	# Judge an existing record by what is stored, so a request cannot reach another mess's
	# record by sending a different mess or hostel with it, and by the request's value too,
	# so a save cannot move a record to a mess the user may not work with.
	stored = _stored_values(doc, [field, hostel_field] if hostel_field else [field])
	if stored:
		stored_mess = stored.get(field)
		if hostel_field and stored.get(hostel_field) in hostels:
			# One of the user's own students; the mess it was filed under may no longer
			# serve their hostel, but keeping that mess is not a move.
			return not value or value == stored_mess or value in messes
		if stored_mess and stored_mess not in messes:
			return False
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


def assert_doc_mess_access(doc, user: str | None = None) -> None:
	"""``assert_mess_access`` for a document being saved.

	A saved record of one of the user's hostels may be edited under the mess it was filed
	under, even after that mess stopped serving the hostel. A new record, or one moved to a
	different mess, needs a mess that serves one of the user's hostels.
	"""
	mess = doc.get("mess")
	hostel_field = HOSTEL_SCOPED_DOCTYPES.get(doc.doctype)
	if mess and hostel_field and not doc.is_new():
		hostels = get_permitted_hostels(user, doc.doctype)
		if hostels is None:
			return
		stored = _stored_values(doc, ["mess", hostel_field])
		if stored and stored.mess == mess and stored.get(hostel_field) in hostels:
			return
	assert_mess_access(mess, user, doc.doctype)
