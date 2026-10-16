"""Room and hostel occupancy counters.

Counters on Hostel Room and Hostel are derived from Hostel Bed rows and are never edited
by hand. Every change to a bed (allotment, transfer, vacate, capacity change) calls
``refresh_room`` and ``refresh_hostel`` so that list views, the room board and reports
read cheap pre-computed values instead of counting beds on every request.
"""

import frappe
from frappe.utils import flt

# Room statuses chosen by staff. Occupancy refreshes keep them until staff change them back.
MANUAL_ROOM_STATUSES = ("Under Maintenance", "Blocked")

# Rooms in these states accept no new allotments.
UNAVAILABLE_ROOM_STATUSES = MANUAL_ROOM_STATUSES


def derive_room_status(occupied: int, total: int) -> str:
	if occupied <= 0:
		return "Available"
	if occupied >= total:
		return "Full"
	return "Partially Occupied"


def _room_bed_counts(room: str) -> dict:
	rows = frappe.db.sql(
		"""
		select status, count(*)
		from `tabHostel Bed`
		where room = %(room)s and is_active = 1
		group by status
		""",
		{"room": room},
	)
	return {status: count for status, count in rows}


def refresh_room(room: str | None) -> None:
	"""Recompute occupied/vacant counts and the occupancy status of a room."""
	if not room:
		return

	current = frappe.db.get_value(
		"Hostel Room", room, ["status", "occupied_beds", "vacant_beds"], as_dict=True
	)
	if not current:
		return

	counts = _room_bed_counts(room)
	total = sum(counts.values())
	occupied = counts.get("Occupied", 0)
	vacant = counts.get("Vacant", 0)

	status = current.status
	if status not in MANUAL_ROOM_STATUSES:
		status = derive_room_status(occupied, total)

	values = {"occupied_beds": occupied, "vacant_beds": vacant, "status": status}
	changed = {key: value for key, value in values.items() if current.get(key) != value}
	if changed:
		frappe.db.set_value("Hostel Room", room, changed, update_modified=False)


def refresh_hostel(hostel: str | None) -> None:
	"""Recompute capacity, occupied and vacant beds of a hostel across its active rooms."""
	if not hostel:
		return

	current = frappe.db.get_value(
		"Hostel",
		hostel,
		["total_capacity", "occupied_beds", "vacant_beds", "occupancy_percent"],
		as_dict=True,
	)
	if not current:
		return

	row = frappe.db.sql(
		"""
		select
			count(bed.name) as total,
			coalesce(sum(bed.status = 'Occupied'), 0) as occupied,
			coalesce(sum(bed.status = 'Vacant' and room.status not in %(unavailable)s), 0) as vacant
		from `tabHostel Bed` bed
		inner join `tabHostel Room` room on room.name = bed.room
		where bed.hostel = %(hostel)s and bed.is_active = 1 and room.is_active = 1
		""",
		{"hostel": hostel, "unavailable": UNAVAILABLE_ROOM_STATUSES},
		as_dict=True,
	)[0]

	total = int(row.total or 0)
	occupied = int(row.occupied or 0)
	values = {
		"total_capacity": total,
		"occupied_beds": occupied,
		"vacant_beds": int(row.vacant or 0),
		"occupancy_percent": flt(occupied * 100.0 / total, 2) if total else 0,
	}
	changed = {key: value for key, value in values.items() if flt(current.get(key)) != flt(value)}
	if changed:
		frappe.db.set_value("Hostel", hostel, changed, update_modified=False)


def refresh_bed_location(bed: str | None) -> None:
	"""Refresh the room and hostel a bed belongs to."""
	if not bed:
		return
	room, hostel = frappe.db.get_value("Hostel Bed", bed, ["room", "hostel"]) or (None, None)
	refresh_room(room)
	refresh_hostel(hostel)


def refresh_all() -> None:
	"""Recompute every room and hostel. Cheap enough to run on each deploy (a few grouped queries)."""
	room_counts = {}
	for room, status, count in frappe.db.sql(
		"""
		select room, status, count(*)
		from `tabHostel Bed`
		where is_active = 1
		group by room, status
		"""
	):
		room_counts.setdefault(room, {})[status] = count

	for room in frappe.get_all(
		"Hostel Room", fields=["name", "status", "occupied_beds", "vacant_beds"], order_by="name asc"
	):
		counts = room_counts.get(room.name, {})
		total = sum(counts.values())
		occupied = counts.get("Occupied", 0)
		status = room.status
		if status not in MANUAL_ROOM_STATUSES:
			status = derive_room_status(occupied, total)
		values = {"occupied_beds": occupied, "vacant_beds": counts.get("Vacant", 0), "status": status}
		changed = {key: value for key, value in values.items() if room.get(key) != value}
		if changed:
			frappe.db.set_value("Hostel Room", room.name, changed, update_modified=False)

	for hostel in frappe.get_all("Hostel", pluck="name"):
		refresh_hostel(hostel)
