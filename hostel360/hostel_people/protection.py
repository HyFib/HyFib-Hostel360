"""Guard fields that only the system may change.

Frappe enforces ``read_only`` in the form, not on the server: a crafted API save could
otherwise point a student at someone else's bed or link a staff record to another
user's account. Controllers call ``keep_system_fields`` from ``validate``; the code
paths that legitimately change these fields either write with ``frappe.db.set_value`` or
set ``doc.flags.allow_system_fields`` before saving.

``allow_system_fields`` should name the fields the caller is changing (a tuple or set).
Only those fields are taken from the document; every other protected field keeps its
stored value, so a whitelisted method running on a copy of the document sent by the
browser cannot smuggle in values for the rest. ``True`` lifts the guard entirely and is
meant for data repair and migration code that rebuilds whole records.
"""

import frappe


def keep_system_fields(doc, fieldnames: tuple[str, ...], defaults: dict | None = None) -> None:
	allowed = doc.flags.allow_system_fields
	# A bare truthy flag (True or 1) keeps its original meaning: no guard at all.
	if (isinstance(allowed, int) and allowed) or frappe.flags.hostel360_migration:
		return
	allowed = _as_set(allowed)
	guarded = [fieldname for fieldname in fieldnames if fieldname not in allowed]
	if not guarded:
		return

	defaults = defaults or {}
	if doc.is_new():
		for fieldname in guarded:
			doc.set(fieldname, defaults.get(fieldname))
		return
	before = doc.get_doc_before_save()
	if not before:
		return
	for fieldname in guarded:
		doc.set(fieldname, before.get(fieldname))


def _as_set(value) -> set[str]:
	if not value:
		return set()
	if isinstance(value, str):
		return {value}
	return set(value)
