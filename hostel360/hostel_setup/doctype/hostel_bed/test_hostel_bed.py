# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.testing import allot, beds_of, make_hostel, make_room, make_student
from hostel360.hostel_setup.doctype.hostel_bed.hostel_bed import set_bed_status


class TestHostelBed(IntegrationTestCase):
	def setUp(self):
		self.hostel = make_hostel("Male")
		self.room = make_room(self.hostel.name, capacity=2)

	def test_beds_are_created_only_through_the_room(self):
		bed = frappe.get_doc({"doctype": "Hostel Bed", "room": self.room.name, "bed_label": "Z"})
		self.assertRaises(frappe.ValidationError, bed.insert)

	def test_beds_are_removed_only_through_the_room(self):
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, "Hostel Bed", beds_of(self.room.name)[1])

	def test_occupied_cannot_be_set_by_hand(self):
		bed = frappe.get_doc("Hostel Bed", beds_of(self.room.name)[0])
		bed.status = "Occupied"
		self.assertRaises(frappe.ValidationError, bed.save)

	def test_reserve_and_release(self):
		bed = beds_of(self.room.name)[0]
		set_bed_status(bed, "Reserved", remarks="Kept for a new admission")
		self.assertEqual(frappe.db.get_value("Hostel Bed", bed, "status"), "Reserved")
		self.assertEqual(frappe.db.get_value("Hostel Room", self.room.name, "vacant_beds"), 1)
		self.assertEqual(frappe.db.get_value("Hostel", self.hostel.name, "vacant_beds"), 1)
		set_bed_status(bed, "Vacant")
		self.assertEqual(frappe.db.get_value("Hostel", self.hostel.name, "vacant_beds"), 2)
		self.assertRaises(frappe.ValidationError, set_bed_status, bed, "Occupied")

	def test_occupied_bed_status_cannot_change(self):
		bed = beds_of(self.room.name)[0]
		allot(make_student("Male").name, bed)
		self.assertRaises(frappe.ValidationError, set_bed_status, bed, "Maintenance")
