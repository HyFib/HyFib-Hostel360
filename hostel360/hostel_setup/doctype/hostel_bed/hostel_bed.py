# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from hostel360.hostel_setup.occupancy import refresh_hostel, refresh_room
from hostel360.hostel_setup.utils import clean_code

# Statuses staff may choose by hand. "Occupied" is set only by Room Allotment.
MANUAL_BED_STATUSES = ("Vacant", "Reserved", "Maintenance")


class HostelBed(Document):
	def before_naming(self):
		self.bed_label = clean_code(self.bed_label, "Bed Label", max_length=4)

	def before_insert(self):
		if not self.flags.from_room_sync:
			frappe.throw(
				_("Beds are created from the room. Increase the capacity of room {0} to add a bed.").format(
					frappe.bold(self.room)
				)
			)

	def validate(self):
		if not self.is_new():
			# Residents are set by Room Allotment directly in the database, never by a form.
			before = self.get_doc_before_save()
			if before:
				for fieldname in (
					"current_student",
					"current_student_name",
					"current_allotment",
					"is_active",
				):
					self.set(fieldname, before.get(fieldname))
		room = frappe.db.get_value("Hostel Room", self.room, ["hostel", "block", "floor"], as_dict=True)
		self.hostel, self.block, self.floor = room.hostel, room.block, room.floor

		if self.status == "Occupied" and not self.current_allotment:
			frappe.throw(_("A bed becomes Occupied only through a Room Allotment."))

		if self.current_allotment and self.status != "Occupied":
			frappe.throw(
				_("Bed {0} is allotted to {1}. Vacate or transfer the allotment first.").format(
					frappe.bold(self.name), frappe.bold(self.current_student_name or self.current_student)
				)
			)

		if not self.is_active and self.current_student:
			frappe.throw(_("An occupied bed cannot be deactivated."))

	def on_update(self):
		if not self.flags.from_room_sync:
			refresh_room(self.room)
			refresh_hostel(self.hostel)

	def on_trash(self):
		if not self.flags.from_room_sync:
			frappe.throw(
				_(
					"Reduce the capacity of room {0} to remove a bed. Beds with history are kept for the record."
				).format(frappe.bold(self.room))
			)
		if self.current_student or self.status == "Occupied":
			frappe.throw(_("Bed {0} is occupied and cannot be removed.").format(frappe.bold(self.name)))


@frappe.whitelist(methods=["POST"])
def set_bed_status(bed: str, status: str, remarks: str | None = None) -> str:
	"""Reserve a vacant bed, send it for maintenance, or release it again (room board action)."""
	if status not in MANUAL_BED_STATUSES:
		frappe.throw(_("Status must be one of {0}.").format(", ".join(_(s) for s in MANUAL_BED_STATUSES)))

	doc = frappe.get_doc("Hostel Bed", bed)
	doc.check_permission("write")
	if doc.current_allotment:
		frappe.throw(
			_("Bed {0} is occupied. Vacate or transfer the allotment first.").format(frappe.bold(bed))
		)
	doc.status = status
	if remarks:
		doc.remarks = remarks
	doc.save()
	return doc.status
