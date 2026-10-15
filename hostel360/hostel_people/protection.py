"""Guard fields that only the system may change.

Frappe enforces ``read_only`` in the form, not on the server: a crafted API save could
otherwise point a student at someone else's bed or link a staff record to another
user's account. Controllers call ``keep_system_fields`` from ``validate``; the code
paths that legitimately change these fields set ``doc.flags.allow_system_fields`` or
write with ``frappe.db.set_value``.
"""

import frappe


def keep_system_fields(doc, fieldnames: tuple[str, ...], defaults: dict | None = None) -> None:
	if doc.flags.allow_system_fields or frappe.flags.hostel360_migration:
		return
	defaults = defaults or {}
	if doc.is_new():
		for fieldname in fieldnames:
			doc.set(fieldname, defaults.get(fieldname))
		return
	before = doc.get_doc_before_save()
	if not before:
		return
	for fieldname in fieldnames:
		doc.set(fieldname, before.get(fieldname))
