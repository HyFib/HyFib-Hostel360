"""Gate pass: check students out and back in against their approved home visit leave.

Used on a tablet at the hostel gate. Gate staff (Hostel Security) cannot open student
records, so everything the gate needs comes from here: a student's name, register number,
room and photo, and the leave that matters today. Everything is limited to the hostels
(and blocks) the signed-in user is posted to. Check-out and return run the leave's own
actions, which apply the same limits and the leave's rules again.
"""

from mimetypes import guess_type

import frappe
from frappe import _
from frappe.query_builder import Criterion, DocType
from pypika.terms import ValueWrapper
from frappe.utils import add_days, flt, get_datetime, getdate, now_datetime, time_diff_in_hours

from hostel360.setup.roles import ADMIN_ROLE, DEPUTY_WARDEN_ROLE, SECURITY_ROLE, WARDEN_ROLE
from hostel360.student_services.doctype.home_visit_leave.home_visit_leave import (
	EARLY_CHECK_OUT_MINUTES,
	MOVEMENT_ACTIONS,
	check_out_opens_at,
)
from hostel360.student_services.utils import get_staff_scope, scope_covers_record

LEAVE = "Home Visit Leave"
STUDENT = "Hostel Student"
PAGE_ROLES = frozenset({SECURITY_ROLE, DEPUTY_WARDEN_ROLE, WARDEN_ROLE, ADMIN_ROLE})
AWAY_STATUSES = ("Checked Out", "Overdue")
GATE_STATUSES = ("Approved", *AWAY_STATUSES)
SEARCH_LIMIT = 20
BOARD_LIMIT = 200
MIN_SEARCH_LENGTH = 2
MAX_SEARCH_LENGTH = 60
PHOTO_EXTENSIONS = ("jpg", "jpeg", "png", "webp")


# ---------------------------------------------------------------------- endpoints


@frappe.whitelist()
def get_board() -> dict:
	"""Students due to leave today and students who are away, for the user's hostels."""
	scope = gate_scope()
	now = now_datetime()
	leaving = board_rows(scope, leaving_condition(now))
	# Whoever is due back soonest (the overdue, mostly) comes first; overdue always does.
	away = board_rows(scope, DocType(LEAVE).status.isin(AWAY_STATUSES), order_by="to_datetime")
	away.sort(key=lambda row: row.status != "Overdue")
	return {
		"now": str(now),
		"hostels": scope_hostels(scope),
		"leaving": [leave_card(row, now) for row in leaving],
		"away": [leave_card(row, now) for row in away],
		"limit": BOARD_LIMIT,
	}


@frappe.whitelist()
def search(query: str | None = None) -> dict:
	"""Students of the user's hostels by register number, roll number, name or room number."""
	scope = gate_scope()
	now = now_datetime()
	text = " ".join((query or "").split())[:MAX_SEARCH_LENGTH]
	if len(text) < MIN_SEARCH_LENGTH or scope == {}:
		return {"now": str(now), "results": []}

	students = find_students(scope, text)
	leaves = gate_leaves(scope, [row.name for row in students])
	results = [student_card(row, leaves.get(row.name, []), now) for row in students]
	return {"now": str(now), "results": results}


@frappe.whitelist(methods=["POST"])
def record_movement(leave: str, action: str) -> dict:
	"""Check a student out, or record their return, at the server's current time."""
	gate_scope()
	if action not in MOVEMENT_ACTIONS:
		frappe.throw(_("Unknown action."))
	doc = frappe.get_doc(LEAVE, leave)
	if action == "check_out":
		doc.mark_checked_out()
	else:
		doc.mark_returned()

	row = board_rows(get_staff_scope(frappe.session.user), DocType(LEAVE).name == doc.name)
	return {"status": doc.status, "card": leave_card(row[0], now_datetime()) if row else None}


@frappe.whitelist(methods=["GET"])
def student_photo(student: str) -> None:
	"""A student's photo, so the gate can see who is in front of it (photos are private files)."""
	scope = gate_scope()
	row = frappe.db.get_value(STUDENT, student, ["name", "current_hostel", "photo"], as_dict=True)
	if not row or not row.current_hostel or not scope_covers_record(scope, row.current_hostel, row.name):
		frappe.throw(_("This student is not in your hostels."), frappe.PermissionError)
	extension = (row.photo or "").rsplit(".", 1)[-1].lower()
	file_name = (
		frappe.db.get_value("File", {"file_url": row.photo, "is_folder": 0}, "name") if row.photo else None
	)
	if not file_name or extension not in PHOTO_EXTENSIONS:
		frappe.throw(_("No photo is on record."), frappe.DoesNotExistError)

	# Read the bytes directly: File.get_content() may try to decode them as text.
	with open(frappe.get_doc("File", file_name).get_full_path(), "rb") as fh:
		content = fh.read()
	frappe.local.response.filename = f"photo.{extension}"
	frappe.local.response.filecontent = content
	frappe.local.response.content_type = guess_type(f"photo.{extension}")[0] or "application/octet-stream"
	frappe.local.response.display_content_as = "inline"
	frappe.local.response.type = "download"
	headers = getattr(frappe.local, "response_headers", None)
	if headers is not None:
		headers.set("Cache-Control", "private, max-age=3600")


# ---------------------------------------------------------------------- access


def gate_scope() -> dict | None:
	"""Scope of the signed-in gate user (None = every hostel); refuses anyone else."""
	user = frappe.session.user
	if user != "Administrator" and not PAGE_ROLES & set(frappe.get_roles(user)):
		frappe.throw(_("Only gate staff and wardens can use the gate pass."), frappe.PermissionError)
	return get_staff_scope(user)


def scope_hostels(scope: dict | None) -> list[str]:
	"""Hostel codes the user is posted to, or an empty list for every hostel."""
	return sorted(scope or {})


def scope_condition(scope: dict | None, hostel_field, student, room):
	"""SQL condition limiting records to the scope's hostels, and to its blocks where set.

	Block assignments cover the students living in that block now, so a record from a hostel
	the student has since left is outside a block-level scope.
	"""
	if scope is None:
		return None
	if not scope:
		return ValueWrapper(1) == ValueWrapper(0)
	terms = []
	for hostel, blocks in scope.items():
		term = hostel_field == hostel
		if blocks is not None:
			term = term & (student.current_hostel == hostel) & room.block.isin(sorted(blocks))
		terms.append(term)
	return Criterion.any(terms)


# ---------------------------------------------------------------------- queries


def leaving_condition(now):
	"""Approved leaves starting before midnight that have not ended."""
	leave = DocType(LEAVE)
	tomorrow = get_datetime(add_days(getdate(now), 1))
	return (leave.status == "Approved") & (leave.from_datetime < tomorrow) & (leave.to_datetime > now)


def board_rows(scope: dict | None, condition, order_by: str = "from_datetime") -> list:
	if scope == {}:
		return []
	leave, student, room = DocType(LEAVE), DocType(STUDENT), DocType("Hostel Room")
	query = (
		frappe.qb.from_(leave)
		.join(student)
		.on(student.name == leave.student)
		.left_join(room)
		.on(room.name == student.current_room)
		.select(
			leave.name,
			leave.student,
			leave.student_name,
			leave.hostel,
			leave.from_datetime,
			leave.to_datetime,
			leave.destination,
			leave.status,
			leave.checked_out_on,
			student.full_name,
			student.current_hostel,
			student.photo,
			room.room_number,
		)
		.where(condition)
		.orderby(getattr(leave, order_by))
		.limit(BOARD_LIMIT)
	)
	within = scope_condition(scope, leave.hostel, student, room)
	if within is not None:
		query = query.where(within)
	return query.run(as_dict=True)


def find_students(scope: dict | None, text: str) -> list:
	student, room = DocType(STUDENT), DocType("Hostel Room")
	prefix = like_escape(text) + "%"
	anywhere = "%" + like_escape(text) + "%"
	matches = [student.name.like(prefix), student.full_name.like(anywhere), room.room_number.like(prefix)]
	if frappe.get_meta(STUDENT).has_field("roll_number"):
		matches.append(student.roll_number.like(prefix))
	query = (
		frappe.qb.from_(student)
		.left_join(room)
		.on(room.name == student.current_room)
		.select(student.name, student.full_name, student.current_hostel, student.photo, room.room_number)
		.where(student.current_hostel.isnotnull() & (student.current_hostel != ""))
		.where(Criterion.any(matches))
		.orderby(student.full_name)
		.limit(SEARCH_LIMIT)
	)
	within = scope_condition(scope, student.current_hostel, student, room)
	if within is not None:
		query = query.where(within)
	return query.run(as_dict=True)


def gate_leaves(scope: dict | None, students: list[str]) -> dict[str, list]:
	"""Approved and away leaves of these students that the user may act on, by student."""
	if not students:
		return {}
	filters = {"student": ["in", students], "status": ["in", GATE_STATUSES]}
	if scope is not None:
		filters["hostel"] = ["in", scope_hostels(scope)]
	rows = frappe.get_all(
		LEAVE,
		filters=filters,
		fields=[
			"name",
			"student",
			"hostel",
			"from_datetime",
			"to_datetime",
			"destination",
			"status",
			"checked_out_on",
		],
		order_by="from_datetime asc",
		ignore_permissions=True,
	)
	by_student: dict[str, list] = {}
	for row in rows:
		by_student.setdefault(row.student, []).append(row)
	return by_student


def like_escape(text: str) -> str:
	return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------- cards


def student_card(row, leaves: list, now) -> dict:
	"""A search result: the student and the one leave the gate needs to know about today."""
	away = next((leave for leave in leaves if leave.status in AWAY_STATUSES), None)
	tomorrow = get_datetime(add_days(getdate(now), 1))
	approved = [
		leave for leave in leaves if leave.status == "Approved" and get_datetime(leave.to_datetime) > now
	]
	today = next((leave for leave in approved if get_datetime(leave.from_datetime) < tomorrow), None)
	upcoming = next((leave for leave in approved if get_datetime(leave.from_datetime) >= tomorrow), None)
	current = away or today
	return {
		**student_fields(row),
		"leave": leave_details(current, now) if current else None,
		"upcoming": (
			{
				"name": upcoming.name,
				"from_datetime": upcoming.from_datetime,
				"to_datetime": upcoming.to_datetime,
			}
			if upcoming and not current
			else None
		),
	}


def leave_card(row, now) -> dict:
	"""A board entry: a leave with the student it belongs to."""
	student = student_fields(frappe._dict(row, name=row.student))
	return {**student, "leave": leave_details(row, now), "upcoming": None}


def student_fields(row) -> dict:
	return {
		"student": row.name,
		"student_name": row.full_name or row.get("student_name") or row.name,
		"hostel": row.current_hostel,
		"room_number": row.room_number,
		"has_photo": bool(row.photo),
	}


def leave_details(leave, now) -> dict:
	"""What the gate may do with the leave now, and if nothing, why not."""
	start, end = get_datetime(leave.from_datetime), get_datetime(leave.to_datetime)
	action, note = None, None
	if leave.status == "Approved":
		opens_at = check_out_opens_at(start)
		if now >= end:
			note = _("This leave has ended. The student needs a new leave to go out.")
		elif now < opens_at:
			note = _("Check-out opens at {0} ({1} minutes before the leave starts).").format(
				frappe.utils.format_datetime(opens_at, "HH:mm"), EARLY_CHECK_OUT_MINUTES
			)
		else:
			action = "check_out"
	elif leave.status in AWAY_STATUSES:
		action = "return"
		if not leave.checked_out_on:
			note = _("No check-out was recorded for this leave.")

	overdue = now > end and leave.status in AWAY_STATUSES
	return {
		"name": leave.name,
		"status": leave.status,
		"from_datetime": leave.from_datetime,
		"to_datetime": leave.to_datetime,
		"destination": leave.destination,
		"checked_out_on": leave.checked_out_on,
		"action": action,
		"note": note,
		"overdue_hours": flt(time_diff_in_hours(now, end), 1) if overdue else None,
	}
