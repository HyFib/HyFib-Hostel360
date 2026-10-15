# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Data and actions behind the Room Board desk page.

The board loads every bed of the selected hostels in one aggregated query (about 1,500
beds for ACGCET) and filters by status, room type and search text in the browser, so
changing a filter costs no round trip.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from hostel360.hostel_setup.occupancy import UNAVAILABLE_ROOM_STATUSES
from hostel360.hostel_setup.utils import bed_label_key, natural_key

COMPATIBLE_HOSTEL_GENDERS = {"Male": ("Male", "Mixed"), "Female": ("Female", "Mixed")}


def permitted_hostels(active_only: bool = True) -> list[dict]:
	"""Hostels the user may see; ``frappe.get_list`` applies hostel User Permissions."""
	filters = {"is_active": 1} if active_only else {}
	return frappe.get_list(
		"Hostel",
		filters=filters,
		fields=["name", "hostel_name", "gender", "total_capacity", "occupied_beds", "vacant_beds"],
		order_by="hostel_name asc",
		limit_page_length=0,
	)


@frappe.whitelist()
def get_board(hostel: str | None = None, block: str | None = None, floor: str | None = None) -> dict:
	frappe.has_permission("Hostel Bed", "read", throw=True)

	hostels = permitted_hostels()
	by_name = {row.name: row for row in hostels}
	if hostel:
		if hostel not in by_name:
			frappe.throw(_("You are not permitted to view hostel {0}.").format(hostel), frappe.PermissionError)
		selected = [hostel]
	else:
		selected = list(by_name)

	result = {
		"hostels": [],
		"permitted_hostels": [{"name": h.name, "hostel_name": h.hostel_name} for h in hostels],
		"summary": {"total": 0, "Vacant": 0, "Occupied": 0, "Reserved": 0, "Maintenance": 0},
		"permissions": {
			"allot": bool(
				frappe.has_permission("Room Allotment", "create")
				and frappe.has_permission("Room Allotment", "submit")
			),
			"edit_beds": bool(frappe.has_permission("Hostel Bed", "write")),
			"students": bool(frappe.has_permission("Hostel Student", "read")),
		},
		"loaded_at": now_datetime(),
	}
	if not selected:
		return result

	conditions = ["bed.hostel in %(hostels)s", "bed.is_active = 1", "room.is_active = 1"]
	values = {"hostels": tuple(selected)}
	if block:
		conditions.append("room.block = %(block)s")
		values["block"] = block
	if floor:
		conditions.append("room.floor = %(floor)s")
		values["floor"] = floor

	show_students = result["permissions"]["students"]
	student_columns = (
		"student.register_number, student.full_name as student_name, student.gender as student_gender,"
		if show_students
		else "null as register_number, null as student_name, null as student_gender,"
	)

	rows = frappe.db.sql(
		f"""
		select
			bed.name as bed, bed.bed_label, bed.status as bed_status, bed.current_allotment,
			bed.current_student, {student_columns}
			room.name as room, room.room_number, room.room_type, room.status as room_status,
			room.capacity, room.occupied_beds, room.hostel,
			coalesce(room.block, '') as block, block.block_name,
			coalesce(room.floor, '') as floor, floor.floor_number, floor.floor_label
		from `tabHostel Bed` bed
		inner join `tabHostel Room` room on room.name = bed.room
		left join `tabHostel Block` block on block.name = room.block
		left join `tabHostel Floor` floor on floor.name = room.floor
		left join `tabHostel Student` student on student.name = bed.current_student
		where {" and ".join(conditions)}
		""",
		values,
		as_dict=True,
	)

	tree = {}
	summary = result["summary"]
	for row in rows:
		summary["total"] += 1
		summary[row.bed_status] = summary.get(row.bed_status, 0) + 1

		hostel_node = tree.setdefault(row.hostel, {})
		block_node = hostel_node.setdefault(
			row.block, {"name": row.block, "block_name": row.block_name or _("Rooms"), "floors": {}}
		)
		floor_node = block_node["floors"].setdefault(
			row.floor,
			{
				"name": row.floor,
				"floor_label": row.floor_label or (_("Rooms") if not row.floor else row.floor),
				"floor_number": row.floor_number if row.floor_number is not None else -99,
				"rooms": {},
			},
		)
		room_node = floor_node["rooms"].setdefault(
			row.room,
			{
				"name": row.room,
				"room_number": row.room_number,
				"room_type": row.room_type,
				"status": row.room_status,
				"capacity": row.capacity,
				"occupied": row.occupied_beds,
				"beds": [],
			},
		)
		room_node["beds"].append(
			{
				"name": row.bed,
				"label": row.bed_label,
				"status": row.bed_status,
				"allotment": row.current_allotment,
				"student": row.current_student if show_students else None,
				"student_name": row.student_name,
				"register_number": row.register_number,
				"gender": row.student_gender,
			}
		)

	for name in sorted(tree, key=lambda h: (by_name[h].hostel_name or h).lower()):
		hostel_row = by_name[name]
		blocks = []
		for block_node in sorted(tree[name].values(), key=lambda b: natural_key(b["block_name"])):
			floors = []
			for floor_node in sorted(block_node["floors"].values(), key=lambda f: f["floor_number"]):
				rooms = sorted(floor_node["rooms"].values(), key=lambda r: natural_key(r["room_number"]))
				for room in rooms:
					room["beds"].sort(key=lambda b: bed_label_key(b["label"]))
				floors.append({**floor_node, "rooms": rooms})
			blocks.append({**block_node, "floors": floors})
		result["hostels"].append(
			{
				"name": name,
				"hostel_name": hostel_row.hostel_name,
				"gender": hostel_row.gender,
				"total": hostel_row.total_capacity,
				"occupied": hostel_row.occupied_beds,
				"vacant": hostel_row.vacant_beds,
				"blocks": blocks,
			}
		)
	return result


@frappe.whitelist(methods=["POST"])
def allot(
	bed: str,
	student: str,
	from_date: str | None = None,
	to_date: str | None = None,
	remarks: str | None = None,
) -> str:
	from hostel360.hostel_people.doctype.room_allotment.room_allotment import allot_bed

	frappe.has_permission("Room Allotment", "create", throw=True)
	return allot_bed(student=student, bed=bed, from_date=from_date, to_date=to_date, remarks=remarks)


@frappe.whitelist(methods=["POST"])
def transfer(allotment: str, new_bed: str, from_date: str | None = None, remarks: str | None = None) -> str:
	doc = frappe.get_doc("Room Allotment", allotment)
	doc.check_permission("read")
	return doc.transfer(new_bed=new_bed, from_date=from_date, remarks=remarks)


@frappe.whitelist(methods=["POST"])
def vacate(allotment: str, vacated_on: str | None = None, reason: str | None = None) -> None:
	doc = frappe.get_doc("Room Allotment", allotment)
	doc.check_permission("read")
	doc.vacate(vacated_on=vacated_on, reason=reason)


@frappe.whitelist(methods=["POST"])
def set_bed_status(bed: str, status: str, remarks: str | None = None) -> str:
	from hostel360.hostel_setup.doctype.hostel_bed.hostel_bed import set_bed_status as _set_bed_status

	return _set_bed_status(bed=bed, status=status, remarks=remarks)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_vacant_beds(doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict):
	"""Link query: vacant, allottable beds in hostels the user may see (and the student may live in)."""
	frappe.has_permission("Hostel Bed", "read", throw=True)
	filters = frappe._dict(frappe.parse_json(filters) if isinstance(filters, str) else (filters or {}))

	hostels = {row.name: row for row in permitted_hostels()}
	if filters.get("student"):
		gender = frappe.db.get_value("Hostel Student", filters.student, "gender")
		allowed_genders = COMPATIBLE_HOSTEL_GENDERS.get(gender, ("Mixed",))
		hostels = {name: row for name, row in hostels.items() if row.gender in allowed_genders}
	if filters.get("hostel"):
		hostels = {name: row for name, row in hostels.items() if name == filters.hostel}
	if not hostels:
		return []

	conditions = [
		"bed.hostel in %(hostels)s",
		"bed.status = 'Vacant'",
		"bed.is_active = 1",
		"ifnull(bed.current_allotment, '') = ''",
		"room.is_active = 1",
		"room.status not in %(unavailable)s",
	]
	values = {
		"hostels": tuple(hostels),
		"unavailable": UNAVAILABLE_ROOM_STATUSES,
		"start": start,
		"page_len": page_len,
	}
	if filters.get("room"):
		conditions.append("bed.room = %(room)s")
		values["room"] = filters.room
	if filters.get("exclude"):
		conditions.append("bed.name != %(exclude)s")
		values["exclude"] = filters.exclude
	if txt:
		conditions.append("(bed.name like %(txt)s or room.room_number like %(txt)s)")
		values["txt"] = f"%{txt}%"

	rows = frappe.db.sql(
		f"""
		select bed.name, bed.hostel, room.room_number, room.room_type, bed.bed_label
		from `tabHostel Bed` bed
		inner join `tabHostel Room` room on room.name = bed.room
		where {" and ".join(conditions)}
		order by bed.hostel, length(room.room_number), room.room_number, length(bed.bed_label), bed.bed_label
		limit %(start)s, %(page_len)s
		""",
		values,
		as_dict=True,
	)
	return [
		(
			row.name,
			_("{0} · Room {1} ({2}) · Bed {3}").format(
				hostels[row.hostel].hostel_name, row.room_number, _(row.room_type), row.bed_label
			),
		)
		for row in rows
	]
