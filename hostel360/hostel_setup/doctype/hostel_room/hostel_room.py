# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from hostel360.hostel_setup.occupancy import MANUAL_ROOM_STATUSES, refresh_hostel, refresh_room
from hostel360.hostel_setup.utils import bed_label_key, bed_label_sequence, clean_code

# Beds a room of each type holds. Dormitories are sized freely.
ROOM_TYPE_CAPACITY = {"Single": 1, "Double": 2, "Triple": 3, "Four Sharing": 4}
MAX_CAPACITY = 60


class HostelRoom(Document):
	def before_naming(self):
		self.room_number = clean_code(self.room_number, "Room Number", max_length=12)

	def validate(self):
		if self.is_new():
			self.room_number = clean_code(self.room_number, "Room Number", max_length=12)
		self.validate_location()
		self.validate_capacity()
		self.validate_status()

	def validate_location(self):
		if self.floor:
			floor_block = frappe.db.get_value("Hostel Floor", self.floor, "block")
			if not self.block:
				self.block = floor_block
			elif floor_block != self.block:
				frappe.throw(
					_("Floor {0} belongs to block {1}, not {2}.").format(
						frappe.bold(self.floor), frappe.bold(floor_block), frappe.bold(self.block)
					)
				)

		if self.block:
			block_hostel = frappe.db.get_value("Hostel Block", self.block, "hostel")
			if block_hostel != self.hostel:
				frappe.throw(
					_("Block {0} belongs to hostel {1}, not {2}.").format(
						frappe.bold(self.block), frappe.bold(block_hostel), frappe.bold(self.hostel)
					)
				)

	def validate_capacity(self):
		self.capacity = cint(self.capacity)
		expected = ROOM_TYPE_CAPACITY.get(self.room_type)
		if expected and self.capacity != expected:
			frappe.throw(
				_("A {0} room holds {1} bed(s). Change the room type to change the capacity.").format(
					_(self.room_type), expected
				)
			)
		if self.room_type == "Dormitory" and self.capacity < 2:
			frappe.throw(_("A dormitory must have at least 2 beds."))
		if not 1 <= self.capacity <= MAX_CAPACITY:
			frappe.throw(_("Capacity must be between 1 and {0}.").format(MAX_CAPACITY))

		if self.is_new():
			return

		occupied = self.get_occupied_bed_count()
		if self.capacity < occupied:
			frappe.throw(
				_("{0} beds in room {1} are occupied, so its capacity cannot be less than {0}.").format(
					occupied, frappe.bold(self.name)
				),
				title=_("Beds Occupied"),
			)

	def validate_status(self):
		occupied = 0 if self.is_new() else self.get_occupied_bed_count()

		if not self.is_active and occupied:
			frappe.throw(
				_("Room {0} has {1} resident(s). Vacate or transfer them before deactivating the room.").format(
					frappe.bold(self.name), occupied
				)
			)

		if self.status == "Blocked" and occupied:
			frappe.throw(
				_("Room {0} has residents and cannot be blocked. Use Under Maintenance to stop new allotments.").format(
					frappe.bold(self.name)
				)
			)

		if self.status not in MANUAL_ROOM_STATUSES and not occupied:
			# Occupancy statuses are recalculated from the beds after save; a hand-picked
			# "Full" on an empty room would hide it from allotment until then.
			self.status = "Available"

	def get_occupied_bed_count(self) -> int:
		return frappe.db.count(
			"Hostel Bed", {"room": self.name, "is_active": 1, "status": "Occupied"}
		) + frappe.db.count(
			"Hostel Bed",
			{"room": self.name, "is_active": 1, "status": ["!=", "Occupied"], "current_student": ["is", "set"]},
		)

	def on_update(self):
		self.sync_beds()
		if self.get_doc_before_save() and (self.has_value_changed("block") or self.has_value_changed("floor")):
			frappe.db.set_value(
				"Hostel Bed",
				{"room": self.name},
				{"block": self.block, "floor": self.floor},
				update_modified=False,
			)
		refresh_room(self.name)
		refresh_hostel(self.hostel)
		# Return the recomputed counters to the form that saved the room.
		self.update(
			frappe.db.get_value("Hostel Room", self.name, ["status", "occupied_beds", "vacant_beds"], as_dict=True)
		)

	def sync_beds(self) -> None:
		"""Make the number of active beds match the capacity.

		New beds are labelled A, B, C...; beds removed by a capacity reduction are deleted
		when they were never used, or kept inactive when allotment history points at them.
		"""
		beds = frappe.get_all(
			"Hostel Bed",
			filters={"room": self.name},
			fields=["name", "bed_label", "status", "is_active", "current_student", "current_allotment"],
		)
		active = [bed for bed in beds if bed.is_active]
		target = cint(self.capacity)

		if len(active) < target:
			needed = target - len(active)
			for bed in sorted((b for b in beds if not b.is_active), key=lambda b: bed_label_key(b.bed_label)):
				if not needed:
					break
				frappe.db.set_value(
					"Hostel Bed", bed.name, {"is_active": 1, "status": "Vacant"}, update_modified=True
				)
				needed -= 1

			used_labels = {bed.bed_label for bed in beds}
			for label in bed_label_sequence():
				if not needed:
					break
				if label in used_labels:
					continue
				bed = frappe.get_doc(
					{
						"doctype": "Hostel Bed",
						"room": self.name,
						"bed_label": label,
						"hostel": self.hostel,
						"block": self.block,
						"floor": self.floor,
						"status": "Vacant",
						"is_active": 1,
					}
				)
				bed.flags.from_room_sync = True
				bed.insert(ignore_permissions=True)
				needed -= 1

		elif len(active) > target:
			excess = len(active) - target
			removable = sorted(
				(
					bed
					for bed in active
					if bed.status != "Occupied" and not bed.current_student and not bed.current_allotment
				),
				key=lambda b: bed_label_key(b.bed_label),
				reverse=True,
			)
			if len(removable) < excess:
				frappe.throw(_("Not enough unoccupied beds to reduce the capacity of room {0}.").format(self.name))

			for bed in removable[:excess]:
				if frappe.db.exists("Room Allotment", {"bed": bed.name}):
					frappe.db.set_value("Hostel Bed", bed.name, {"is_active": 0, "status": "Vacant"})
				else:
					bed_doc = frappe.get_doc("Hostel Bed", bed.name)
					bed_doc.flags.from_room_sync = True
					bed_doc.delete(ignore_permissions=True)

	def on_trash(self):
		if self.get_occupied_bed_count():
			frappe.throw(_("Room {0} has residents and cannot be deleted.").format(frappe.bold(self.name)))
		if frappe.db.exists("Room Allotment", {"room": self.name}):
			frappe.throw(
				_("Room {0} has allotment history and cannot be deleted. Untick Is Active instead.").format(
					frappe.bold(self.name)
				)
			)
		for bed in frappe.get_all("Hostel Bed", filters={"room": self.name}, pluck="name"):
			bed_doc = frappe.get_doc("Hostel Bed", bed)
			bed_doc.flags.from_room_sync = True
			bed_doc.delete(ignore_permissions=True)

	def after_delete(self):
		refresh_hostel(self.hostel)

	@frappe.whitelist()
	def get_beds(self) -> list[dict]:
		"""Beds with their residents, for the bed strip on the room form."""
		self.check_permission("read")
		beds = frappe.get_all(
			"Hostel Bed",
			filters={"room": self.name, "is_active": 1},
			fields=["name", "bed_label", "status", "current_student", "current_student_name", "current_allotment"],
		)
		return sorted(beds, key=lambda b: bed_label_key(b.bed_label))
