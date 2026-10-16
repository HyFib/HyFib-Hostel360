# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

# Leaves that can mean a student is away. "Returned" stays in the set because a return recorded
# with a future time (a parent calling ahead) still leaves the student away until then.
AWAY_CANDIDATES = ("Approved", "Checked Out", "Overdue", "Returned")


@frappe.whitelist()
def get_students_on_leave(filters: str | list | dict | None = None) -> dict:
	"""Number of students away from the hostel right now.

	Counts each student once, using the same rule as the Students On Leave report so the card and
	the report it opens always agree: a recorded check-out decides it when there is one, otherwise
	the approved leave period does, and an overdue leave keeps the student away until the return
	is recorded. Extra card filters (for example a hostel) apply to Home Visit Leave.
	"""
	if not frappe.has_permission("Home Visit Leave", "read"):
		frappe.throw(_("You are not permitted to view leave records."), frappe.PermissionError)

	now = now_datetime()
	conditions = [
		["Home Visit Leave", "status", "in", AWAY_CANDIDATES],
		["Home Visit Leave", "from_datetime", "<=", now],
	]
	extra = frappe.parse_json(filters) if filters else []
	if isinstance(extra, dict):
		extra = [["Home Visit Leave", field, "=", value] for field, value in extra.items()]
	conditions.extend(extra or [])

	leaves = frappe.get_list(
		"Home Visit Leave",
		filters=conditions,
		or_filters=[["returned_on", "is", "not set"], ["returned_on", ">", now]],
		fields=["student", "status", "from_datetime", "to_datetime", "checked_out_on", "returned_on"],
		limit_page_length=0,
	)
	away = {leave.student for leave in leaves if is_away(leave, now)}

	return {
		"value": len(away),
		"fieldtype": "Int",
		"route": card_route(),
	}


def card_route() -> list[str]:
	"""Where the card opens: the Students On Leave report, or for people without reports (gate
	security, the mess manager) the gate pass page or the leave list, whichever they may open."""
	if frappe.get_cached_doc("Report", "Students On Leave").is_permitted():
		return ["query-report", "Students On Leave"]
	if frappe.get_cached_doc("Page", "gate-pass").is_permitted():
		return ["gate-pass"]
	return ["List", "Home Visit Leave"]


def is_away(leave, as_of) -> bool:
	returned_on = get_datetime(leave.returned_on) if leave.returned_on else None
	if returned_on and returned_on <= as_of:
		return False
	if leave.checked_out_on:
		return get_datetime(leave.checked_out_on) <= as_of
	if leave.status == "Overdue" or returned_on:
		return True
	return get_datetime(leave.from_datetime) <= as_of < get_datetime(leave.to_datetime)
