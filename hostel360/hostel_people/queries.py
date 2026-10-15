"""Link queries for the registration desk.

Students, guardians and allotments are scoped to the hostels a person looks after (see
``hostel360.hostel_people.permissions``). Two everyday cases fall outside that scope:

* A parent whose other child lives in another hostel. A brother in a boys' hostel and a
  sister in a girls' hostel usually share one parent number, but a guardian is visible
  only through their wards. Staff who register students may link such a guardian by
  typing the guardian's exact mobile number.
* A student coming back after leaving a hostel. A former resident stays with the staff of
  their last hostel, yet next term they often move into another one. Staff who allot beds
  may pick a student who lives in no hostel now by the exact register or mobile number.

Both work only on an identifier the person at the desk has in front of them, never by
browsing, and return just enough to recognise the person. Opening or editing the record
afterwards stays subject to the usual permission rules.
"""

import re

import frappe
from frappe import _
from frappe.desk.search import search_widget

from hostel360.utils.validation import mask_mobile, normalize_mobile

# Students in these statuses may be given a bed (Room Allotment refuses the others).
ALLOTTABLE_STUDENT_STATUSES = ("Active", "Applied")
STUDENT_GENDERS = ("Male", "Female", "Other")


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def guardian_link_query(doctype, txt, searchfield, start, page_len, filters):
	"""Guardian link of the student form's Guardians table.

	The usual search, plus the guardian whose mobile number is exactly ``txt``. Desk also
	validates a chosen value through this query with the guardian's ID as ``txt``; that
	match is returned without the name, so guessing IDs reveals nothing.
	"""
	results = _standard_results("Hostel Guardian", txt, searchfield, start, page_len, filters)
	if not txt or start or not frappe.has_permission("Hostel Student", "write"):
		return results

	listed = {row[0] for row in results}
	mobile = _exact_mobile(txt)
	if mobile:
		for row in frappe.get_all(
			"Hostel Guardian",
			filters={"mobile_number": mobile},
			fields=["name", "guardian_name", "relation", "mobile_number"],
			limit=1,
		):
			if row.name not in listed:
				results.append(
					(
						row.name,
						row.guardian_name,
						_(row.relation) if row.relation else "",
						mask_mobile(row.mobile_number),
					)
				)
	else:
		name = txt.strip()
		if name not in listed and frappe.db.exists("Hostel Guardian", name):
			results.append((name,))
	return results


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def allottable_student_query(doctype, txt, searchfield, start, page_len, filters):
	"""Student link of a new allotment (Room Allotment form and Room Board).

	The usual search, plus a returning student (one who lives in no hostel now) whose
	register number or mobile number is exactly ``txt``. The returning student is found
	with fixed conditions only: the filters sent by the browser are not applied to it, so
	they cannot be used to probe a record the user may not open.
	"""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else filters
	results = _standard_results("Hostel Student", txt, searchfield, start, page_len, filters)
	if not txt or start or not may_allot_beds() or _asks_for_residents(filters):
		return results

	register_number = "".join(txt.split()).upper()
	matches = [["name", "=", register_number]]
	mobile = _exact_mobile(txt)
	if mobile:
		matches.append(["mobile_number", "=", mobile])

	conditions = {
		"status": ["in", ALLOTTABLE_STUDENT_STATUSES],
		"validation_status": ["!=", "Rejected"],
		"current_hostel": ["is", "not set"],
		"current_allotment": ["is", "not set"],
	}
	gender = filters.get("gender") if isinstance(filters, dict) else None
	if gender in STUDENT_GENDERS:
		conditions["gender"] = gender

	listed = {row[0] for row in results}
	for row in frappe.get_all(
		"Hostel Student",
		filters=conditions,
		or_filters=matches,
		fields=["name", "full_name"],
		limit=2,
	):
		if row.name not in listed:
			results.append((row.name, row.full_name, _("Returning student")))
	return results


def may_allot_beds(user: str | None = None) -> bool:
	return bool(
		frappe.has_permission("Room Allotment", "create", user=user)
		and frappe.has_permission("Room Allotment", "submit", user=user)
	)


def _standard_results(doctype, txt, searchfield, start, page_len, filters) -> list:
	"""Frappe's own link search, with every permission rule applied."""
	return list(
		search_widget(
			doctype,
			txt or "",
			searchfield=searchfield,
			start=start,
			page_length=page_len,
			filters=filters,
			as_dict=False,
		)
		or []
	)


def _asks_for_residents(filters) -> bool:
	# A transfer picks a student who holds a bed now, never a returning one.
	return isinstance(filters, dict) and filters.get("current_allotment") == ["is", "set"]


def _exact_mobile(txt: str) -> str | None:
	"""The 10-digit mobile number ``txt`` spells out, or None for any other text."""
	digits = re.sub(r"\D", "", txt or "")
	if len(digits) not in (10, 11, 12) or re.search(r"[^\d\s+\-()]", txt):
		return None
	try:
		return normalize_mobile(digits)
	except frappe.ValidationError:
		# A search must not leave the "not a valid number" message behind for the browser.
		frappe.clear_last_message()
		return None
