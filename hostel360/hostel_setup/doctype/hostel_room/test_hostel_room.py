# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from hostel360.hostel_people.testing import (
	allot,
	beds_of,
	make_block,
	make_floor,
	make_hostel,
	make_room,
	make_student,
)


class TestHostelRoom(IntegrationTestCase):
	def setUp(self):
		self.hostel = make_hostel("Male")

	def labels(self, room):
		return [frappe.db.get_value("Hostel Bed", bed, "bed_label") for bed in beds_of(room)]

	def test_beds_created_with_the_room(self):
		room = make_room(self.hostel.name, capacity=3)
		self.assertEqual(self.labels(room.name), ["A", "B", "C"])
		self.assertEqual(beds_of(room.name)[0], f"{room.name}-A")
		self.assertTrue(room.name.startswith(f"{self.hostel.name}-"))
		self.assertEqual(
			set(frappe.get_all("Hostel Bed", filters={"room": room.name}, pluck="status")), {"Vacant"}
		)
		room.reload()
		self.assertEqual(room.status, "Available")
		self.assertEqual(room.vacant_beds, 3)
		self.assertEqual(frappe.db.get_value("Hostel", self.hostel.name, "total_capacity"), 3)

	def test_capacity_increase_adds_beds(self):
		room = make_room(self.hostel.name, capacity=4, room_type="Dormitory")
		room.capacity = 6
		room.save()
		self.assertEqual(self.labels(room.name), ["A", "B", "C", "D", "E", "F"])
		self.assertEqual(frappe.db.get_value("Hostel", self.hostel.name, "total_capacity"), 6)

	def test_capacity_decrease_removes_unused_beds(self):
		room = make_room(self.hostel.name, capacity=6, room_type="Dormitory")
		room.capacity = 4
		room.save()
		self.assertEqual(self.labels(room.name), ["A", "B", "C", "D"])
		self.assertFalse(frappe.db.exists("Hostel Bed", f"{room.name}-F"))

	def test_occupied_beds_are_never_removed(self):
		room = make_room(self.hostel.name, capacity=3, room_type="Dormitory")
		beds = beds_of(room.name)
		for bed in beds:
			allot(make_student("Male").name, bed)
		room.reload()
		room.capacity = 2
		self.assertRaises(frappe.ValidationError, room.save)
		self.assertEqual(len(beds_of(room.name)), 3)

	def test_reducing_capacity_skips_occupied_beds_and_keeps_history(self):
		room = make_room(self.hostel.name, capacity=4, room_type="Dormitory")
		bed_a, bed_b, bed_c, bed_d = beds_of(room.name)
		allot(make_student("Male").name, bed_a)
		used = allot(make_student("Male").name, bed_c, from_date=add_days(nowdate(), -5))
		used.vacate(vacated_on=nowdate(), reason="Moved out")

		# D was never used and is deleted.
		room.reload()
		room.capacity = 3
		room.save()
		self.assertEqual(beds_of(room.name), [bed_a, bed_b, bed_c])
		self.assertFalse(frappe.db.exists("Hostel Bed", bed_d))

		# C has allotment history: it is kept, but inactive. Occupied A is never touched.
		room.reload()
		room.capacity = 2
		room.save()
		self.assertEqual(beds_of(room.name), [bed_a, bed_b])
		self.assertEqual(frappe.db.get_value("Hostel Bed", bed_c, "is_active"), 0)
		self.assertEqual(frappe.db.get_value("Hostel", self.hostel.name, "total_capacity"), 2)

		# Growing again brings C back before creating new beds.
		room.reload()
		room.capacity = 3
		room.save()
		self.assertEqual(beds_of(room.name), [bed_a, bed_b, bed_c])

	def test_room_type_must_match_capacity(self):
		room = frappe.get_doc(
			{
				"doctype": "Hostel Room",
				"hostel": self.hostel.name,
				"room_number": "X1",
				"room_type": "Double",
				"capacity": 3,
			}
		)
		self.assertRaises(frappe.ValidationError, room.insert)

	def test_block_and_floor_must_belong_to_the_hostel(self):
		block = make_block(self.hostel.name, "A")
		floor = make_floor(block.name, 2)
		room = make_room(self.hostel.name, capacity=1, floor=floor.name)
		self.assertEqual(room.block, block.name)
		self.assertEqual(frappe.db.get_value("Hostel Bed", beds_of(room.name)[0], "floor"), floor.name)

		other = make_hostel("Male")
		other_block = make_block(other.name, "B")
		wrong_block = frappe.get_doc(
			{
				"doctype": "Hostel Room",
				"hostel": self.hostel.name,
				"block": other_block.name,
				"room_number": "Y1",
				"room_type": "Single",
				"capacity": 1,
			}
		)
		self.assertRaises(frappe.ValidationError, wrong_block.insert)

		second_block = make_block(self.hostel.name, "C")
		mismatched = frappe.get_doc(
			{
				"doctype": "Hostel Room",
				"hostel": self.hostel.name,
				"block": second_block.name,
				"floor": floor.name,
				"room_number": "Y2",
				"room_type": "Single",
				"capacity": 1,
			}
		)
		self.assertRaises(frappe.ValidationError, mismatched.insert)

	def test_room_with_residents_cannot_be_deactivated_or_blocked(self):
		room = make_room(self.hostel.name, capacity=2)
		allot(make_student("Male").name, beds_of(room.name)[0])
		room.reload()
		room.is_active = 0
		self.assertRaises(frappe.ValidationError, room.save)
		room.reload()
		room.status = "Blocked"
		self.assertRaises(frappe.ValidationError, room.save)
		room.reload()
		room.status = "Under Maintenance"
		room.save()
		self.assertEqual(frappe.db.get_value("Hostel Room", room.name, "status"), "Under Maintenance")

	def test_room_number_is_normalised(self):
		room = make_room(self.hostel.name, capacity=1, room_number=" g-12 ")
		self.assertEqual(room.room_number, "G-12")
		self.assertEqual(room.name, f"{self.hostel.name}-G-12")
		bad = frappe.get_doc(
			{
				"doctype": "Hostel Room",
				"hostel": self.hostel.name,
				"room_number": "1/2",
				"room_type": "Single",
				"capacity": 1,
			}
		)
		self.assertRaises(frappe.ValidationError, bad.insert)
