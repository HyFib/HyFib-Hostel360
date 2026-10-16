# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

from hostel360.hostel_setup.occupancy import UNAVAILABLE_ROOM_STATUSES
from hostel360.hostel_setup.utils import natural_key

GROUP_LEVELS = ("Hostel", "Block", "Floor", "Room")
CHART_LIMIT = 30


def execute(filters=None):
	filters = frappe._dict(filters or {})
	group_by = filters.get("group_by") if filters.get("group_by") in GROUP_LEVELS else "Block"

	rooms = get_room_rows(filters)
	data = aggregate(rooms, group_by)
	columns = get_columns(group_by)
	chart = get_chart(data, group_by)
	summary = get_summary(data)
	return columns, data, None, chart, summary


def get_room_rows(filters) -> list[dict]:
	hostels = frappe.get_list("Hostel", filters={"is_active": 1}, pluck="name", limit_page_length=0)
	if filters.get("hostel"):
		hostels = [h for h in hostels if h == filters.hostel]
	if not hostels:
		return []

	conditions = ["room.is_active = 1", "room.hostel in %(hostels)s"]
	values = {"hostels": tuple(hostels), "unavailable": UNAVAILABLE_ROOM_STATUSES}
	if filters.get("block"):
		conditions.append("room.block = %(block)s")
		values["block"] = filters.block
	if filters.get("room_type"):
		conditions.append("room.room_type = %(room_type)s")
		values["room_type"] = filters.room_type

	return frappe.db.sql(
		f"""
		select
			room.hostel, hostel.hostel_name, hostel.gender,
			coalesce(room.block, '') as block, block.block_name,
			coalesce(room.floor, '') as floor, floor.floor_label, floor.floor_number,
			room.name as room, room.room_number, room.room_type, room.status as room_status,
			count(bed.name) as beds,
			coalesce(sum(bed.status = 'Occupied'), 0) as occupied,
			coalesce(sum(bed.status = 'Vacant' and room.status not in %(unavailable)s), 0) as vacant,
			coalesce(sum(bed.status = 'Reserved'), 0) as reserved,
			coalesce(sum(bed.status = 'Maintenance' or (bed.status = 'Vacant' and room.status in %(unavailable)s)), 0)
				as unavailable
		from `tabHostel Room` room
		inner join `tabHostel` hostel on hostel.name = room.hostel
		inner join `tabHostel Bed` bed on bed.room = room.name and bed.is_active = 1
		left join `tabHostel Block` block on block.name = room.block
		left join `tabHostel Floor` floor on floor.name = room.floor
		where {" and ".join(conditions)}
		group by room.name
		""",  # noqa: S608 - conditions are fixed strings; values are bound parameters
		values,
		as_dict=True,
	)


def aggregate(rooms: list[dict], group_by: str) -> list[dict]:
	depth = GROUP_LEVELS.index(group_by)
	groups = {}
	for room in rooms:
		key = (room.hostel, room.block, room.floor, room.room)[: depth + 1]
		row = groups.get(key)
		if not row:
			row = groups[key] = frappe._dict(
				hostel=room.hostel,
				hostel_name=room.hostel_name,
				gender=room.gender,
				block=room.block or None,
				block_name=room.block_name or (_("No Block") if depth >= 1 else None),
				floor=room.floor or None,
				floor_label=room.floor_label or (_("No Floor") if depth >= 2 else None),
				floor_number=room.floor_number,
				room=room.room,
				room_number=room.room_number,
				room_type=room.room_type,
				room_status=room.room_status,
				rooms=0,
				beds=0,
				occupied=0,
				vacant=0,
				reserved=0,
				unavailable=0,
			)
		row.rooms += 1
		for field in ("beds", "occupied", "vacant", "reserved", "unavailable"):
			row[field] += int(room[field] or 0)

	data = list(groups.values())
	for row in data:
		row.occupancy_percent = flt(row.occupied * 100.0 / row.beds, 1) if row.beds else 0
		row.label = " · ".join(
			filter(
				None,
				[
					row.hostel_name,
					row.block_name if depth >= 1 else None,
					row.floor_label if depth >= 2 else None,
					row.room_number if depth >= 3 else None,
				],
			)
		)
	data.sort(
		key=lambda r: (
			(r.hostel_name or "").lower(),
			natural_key(r.block_name or ""),
			r.floor_number if r.floor_number is not None else -99,
			natural_key(r.room_number or ""),
		)
	)
	return data


def get_columns(group_by: str) -> list[dict]:
	depth = GROUP_LEVELS.index(group_by)
	columns = [
		{"label": _("Hostel"), "fieldname": "hostel", "fieldtype": "Link", "options": "Hostel", "width": 90},
		{"label": _("Hostel Name"), "fieldname": "hostel_name", "fieldtype": "Data", "width": 180},
		{"label": _("Gender"), "fieldname": "gender", "fieldtype": "Data", "width": 80},
	]
	if depth >= 1:
		columns.append({"label": _("Block"), "fieldname": "block_name", "fieldtype": "Data", "width": 120})
	if depth >= 2:
		columns.append({"label": _("Floor"), "fieldname": "floor_label", "fieldtype": "Data", "width": 120})
	if depth >= 3:
		columns += [
			{
				"label": _("Room"),
				"fieldname": "room",
				"fieldtype": "Link",
				"options": "Hostel Room",
				"width": 110,
			},
			{"label": _("Room Type"), "fieldname": "room_type", "fieldtype": "Data", "width": 110},
			{"label": _("Room Status"), "fieldname": "room_status", "fieldtype": "Data", "width": 140},
		]
	else:
		columns.append({"label": _("Rooms"), "fieldname": "rooms", "fieldtype": "Int", "width": 80})
	columns += [
		{"label": _("Beds"), "fieldname": "beds", "fieldtype": "Int", "width": 80},
		{"label": _("Occupied"), "fieldname": "occupied", "fieldtype": "Int", "width": 90},
		{"label": _("Vacant"), "fieldname": "vacant", "fieldtype": "Int", "width": 80},
		{"label": _("Reserved"), "fieldname": "reserved", "fieldtype": "Int", "width": 90},
		{"label": _("Unavailable"), "fieldname": "unavailable", "fieldtype": "Int", "width": 100},
		{"label": _("Occupancy %"), "fieldname": "occupancy_percent", "fieldtype": "Percent", "width": 110},
	]
	return columns


def get_chart(data: list[dict], group_by: str) -> dict | None:
	if not data:
		return None
	rows = data[:CHART_LIMIT]
	return {
		"data": {
			"labels": [row.label for row in rows],
			"datasets": [
				{"name": _("Occupied"), "values": [row.occupied for row in rows]},
				{"name": _("Vacant"), "values": [row.vacant for row in rows]},
				{
					"name": _("Reserved / Unavailable"),
					"values": [row.reserved + row.unavailable for row in rows],
				},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"colors": ["#2563eb", "#16a34a", "#f59e0b"],
		"fieldtype": "Int",
	}


def get_summary(data: list[dict]) -> list[dict]:
	beds = sum(row.beds for row in data)
	occupied = sum(row.occupied for row in data)
	vacant = sum(row.vacant for row in data)
	other = sum(row.reserved + row.unavailable for row in data)
	percent = flt(occupied * 100.0 / beds, 1) if beds else 0
	return [
		{"value": beds, "label": _("Total Beds"), "datatype": "Int", "indicator": "Blue"},
		{"value": occupied, "label": _("Occupied"), "datatype": "Int", "indicator": "Blue"},
		{"value": vacant, "label": _("Vacant"), "datatype": "Int", "indicator": "Green" if vacant else "Red"},
		{"value": other, "label": _("Reserved / Unavailable"), "datatype": "Int", "indicator": "Orange"},
		{"value": percent, "label": _("Occupancy"), "datatype": "Percent", "indicator": "Blue"},
	]
