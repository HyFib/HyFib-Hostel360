"""Row-level access to students, guardians and allotments in Desk.

User Permissions on Hostel scope staff to their hostels for every DocType with a Hostel
link. What they cannot express is handled here:

* Block assignments. A Hostel Staff assignment with a block limits the person to the
  students living in that block. Hostel Student and Room Allotment records of the other
  blocks of the same hostel are hidden from them.
* Guardians. Hostel Guardian has no Hostel link (siblings may live in different
  hostels), so it is scoped through its wards: a guardian is visible when at least one
  ward is, and may be changed only when every ward is. A guardian's mobile number is
  their portal sign-in, so staff of one hostel must not be able to change it for the
  parent of another hostel's student.
* Stored values. Frappe judges a save by the values the request sent. For a record that
  already exists the hostel (and block) are also read from the database, so a request
  cannot open another hostel's record by rewriting them.

``*_query_conditions`` return SQL for list views and reports; ``*_has_permission`` return
False to deny access to a single record and True to leave the decision to the role and
User Permission rules.
"""

import frappe

from hostel360.hostel_people.services import block_restrictions, get_staff_scope

# Permission types that only look at a record.
READ_PTYPES = frozenset({"read", "select", "print", "email", "export", "report"})

STUDENT_ACCESS_FIELDS = ("current_hostel", "last_hostel", "current_room")
ALLOTMENT_ACCESS_FIELDS = ("hostel", "block")


# ---------------------------------------------------------------------- Hostel Student


def student_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	restricted = block_restrictions(get_staff_scope(user))
	if not restricted:
		return ""
	return _student_block_condition("`tabHostel Student`", restricted)


def student_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	allowed = _permitted_hostels(user, "Hostel Student")
	restricted = block_restrictions(get_staff_scope(user))
	if not allowed and not restricted:
		return True
	stored = _stored_values(doc, STUDENT_ACCESS_FIELDS)
	if stored is not None and not _within(allowed, (stored.current_hostel, stored.last_hostel)):
		return False
	return all(_student_in_blocks(values, restricted) for values in (doc, stored) if values is not None)


def _student_in_blocks(values, restricted: dict) -> bool:
	if not restricted:
		return True
	hostel = values.get("current_hostel")
	if not hostel or hostel not in restricted:
		return True
	room = values.get("current_room")
	block = frappe.db.get_value("Hostel Room", room, "block") if room else None
	return block in restricted[hostel]


# ---------------------------------------------------------------------- Room Allotment


def allotment_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	restricted = block_restrictions(get_staff_scope(user))
	if not restricted:
		return ""
	table = "`tabRoom Allotment`"
	hidden = [
		f"(ifnull({table}.`hostel`, '') = {frappe.db.escape(hostel)}"
		f" and ifnull({table}.`block`, '') not in ({_sql_list(blocks)}))"
		for hostel, blocks in sorted(restricted.items())
	]
	return f"not ({' or '.join(hidden)})"


def allotment_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	allowed = _permitted_hostels(user, "Room Allotment")
	restricted = block_restrictions(get_staff_scope(user))
	if not allowed and not restricted:
		return True
	stored = _stored_values(doc, ALLOTMENT_ACCESS_FIELDS)
	if stored is not None and not _within(allowed, (stored.hostel,)):
		return False
	return all(_allotment_in_blocks(values, restricted) for values in (doc, stored) if values is not None)


def _allotment_in_blocks(values, restricted: dict) -> bool:
	if not restricted:
		return True
	hostel = values.get("hostel")
	if not hostel or hostel not in restricted:
		return True
	return values.get("block") in restricted[hostel]


# ---------------------------------------------------------------------- Hostel Guardian


def guardian_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	user = user or frappe.session.user
	visible = _student_visibility_sql(user, "ward")
	if not visible:
		return ""
	guardian = "`tabHostel Guardian`.`name`"
	# Permission query condition: {visible} is built from escaped values (_student_visibility_sql).
	return (
		"(not exists (select 1 from `tabHostel Student Guardian` link"  # noqa: S608
		f" where link.`guardian` = {guardian} and link.`parenttype` = 'Hostel Student')"
		" or exists (select 1 from `tabHostel Student Guardian` link"
		" join `tabHostel Student` ward on ward.`name` = link.`parent`"
		f" where link.`guardian` = {guardian} and link.`parenttype` = 'Hostel Student' and {visible}))"
	)


def guardian_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if not doc.get("name") or _is_new(doc) or not _student_visibility_sql(user, "ward"):
		return True
	wards = frappe.get_all(
		"Hostel Student Guardian",
		filters={"guardian": doc.get("name"), "parenttype": "Hostel Student"},
		pluck="parent",
		distinct=True,
	)
	if not wards:
		# Not linked yet: the registration desk creates guardians before linking them.
		return True
	readable = [frappe.has_permission("Hostel Student", "read", doc=ward, user=user) for ward in wards]
	if (ptype or "read") in READ_PTYPES:
		return any(readable)
	return all(readable)


# ---------------------------------------------------------------------- helpers


def _student_visibility_sql(user: str, alias: str) -> str:
	"""SQL that is true for students (table alias ``alias``) the user may see, or "" for all.

	Mirrors what Frappe does for Hostel Student itself: User Permissions on Hostel apply to
	every non-empty Hostel link of the student, and block assignments come on top.
	"""
	if user == "Administrator":
		return ""
	hostels = _permitted_hostels(user, "Hostel Student")
	restricted = block_restrictions(get_staff_scope(user))
	conditions = []
	if hostels:
		allowed = _sql_list(hostels)
		for field in ("current_hostel", "last_hostel"):
			conditions.append(f"(ifnull({alias}.`{field}`, '') = '' or {alias}.`{field}` in ({allowed}))")
	if restricted:
		conditions.append(_student_block_condition(alias, restricted))
	return " and ".join(conditions)


def _student_block_condition(table: str, restricted: dict) -> str:
	# ``table`` is a fixed alias from this module; hostel and block names are escaped.
	hidden = [
		f"(ifnull({table}.`current_hostel`, '') = {frappe.db.escape(hostel)}"  # noqa: S608
		" and not exists (select 1 from `tabHostel Room` room"
		f" where room.`name` = {table}.`current_room` and room.`block` in ({_sql_list(blocks)})))"
		for hostel, blocks in sorted(restricted.items())
	]
	return f"not ({' or '.join(hidden)})"


def _permitted_hostels(user: str, doctype: str) -> set[str]:
	"""Hostels the user's User Permissions allow for ``doctype``; empty when unrestricted."""
	from frappe.permissions import get_user_permissions

	return {
		row.get("doc")
		for row in get_user_permissions(user).get("Hostel") or []
		if not row.get("applicable_for") or row.get("applicable_for") == doctype
	}


def _within(allowed: set[str], hostels) -> bool:
	"""Frappe's User Permission rule (no restriction, or empty links, pass) for stored values."""
	return not allowed or all(not hostel or hostel in allowed for hostel in hostels)


def _stored_values(doc, fieldnames) -> frappe._dict | None:
	"""Values of an existing record as saved; None for a record being created."""
	name = doc.get("name")
	if not name or _is_new(doc):
		return None
	return frappe.db.get_value(doc.get("doctype"), name, list(fieldnames), as_dict=True)


def _is_new(doc) -> bool:
	return doc.is_new() if hasattr(doc, "is_new") else bool(doc.get("__islocal"))


def _sql_list(values) -> str:
	return ", ".join(frappe.db.escape(value) for value in sorted(values))
