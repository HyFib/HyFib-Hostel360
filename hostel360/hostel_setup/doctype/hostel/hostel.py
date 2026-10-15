# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from hostel360.hostel_setup.occupancy import refresh_all, refresh_hostel, refresh_room
from hostel360.hostel_setup.utils import clean_code


class Hostel(Document):
	def autoname(self):
		self.hostel_code = clean_code(self.hostel_code, "Hostel Code", max_length=10)
		self.name = self.hostel_code

	def validate(self):
		self.hostel_name = " ".join((self.hostel_name or "").split())
		if self.is_new():
			self.hostel_code = clean_code(self.hostel_code, "Hostel Code", max_length=10)
		self.validate_gender_change()
		self.validate_deactivation()

	def validate_gender_change(self):
		if self.is_new() or not self.has_value_changed("gender") or self.gender == "Mixed":
			return
		mismatched = frappe.db.sql(
			"""
			select student.name, student.full_name
			from `tabRoom Allotment` allotment
			inner join `tabHostel Student` student on student.name = allotment.student
			where allotment.hostel = %(hostel)s and allotment.docstatus = 1
				and allotment.status = 'Active' and student.gender != %(gender)s
			limit 3
			""",
			{"hostel": self.name, "gender": self.gender},
			as_dict=True,
		)
		if mismatched:
			names = ", ".join(f"{row.full_name} ({row.name})" for row in mismatched)
			frappe.throw(
				_("Cannot make this a {0} hostel while other residents are allotted here: {1}").format(
					_(self.gender), names
				)
			)

	def validate_deactivation(self):
		if self.is_new() or self.is_active or not self.has_value_changed("is_active"):
			return
		occupied = frappe.db.count("Hostel Bed", {"hostel": self.name, "status": "Occupied"})
		if occupied:
			frappe.throw(
				_(
					"{0} beds in this hostel are occupied. Vacate or transfer the residents before deactivating it."
				).format(occupied)
			)

	def on_update(self):
		refresh_hostel(self.name)

	@frappe.whitelist(methods=["POST"])
	def recalculate_occupancy(self) -> dict:
		"""Rebuild room and hostel counters from the beds (used after bulk corrections)."""
		self.check_permission("read")
		for room in frappe.get_all("Hostel Room", filters={"hostel": self.name}, pluck="name"):
			refresh_room(room)
		refresh_hostel(self.name)
		return frappe.db.get_value(
			"Hostel",
			self.name,
			["total_capacity", "occupied_beds", "vacant_beds", "occupancy_percent"],
			as_dict=True,
		)


@frappe.whitelist(methods=["POST"])
def recalculate_all_occupancy() -> None:
	frappe.only_for(["Hostel Administrator", "System Manager"])
	refresh_all()
