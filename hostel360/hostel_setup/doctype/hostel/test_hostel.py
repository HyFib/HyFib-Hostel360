# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.testing import allot, beds_of, make_hostel, make_room, make_student
from hostel360.hostel_setup.occupancy import refresh_all, refresh_hostel


class TestHostelOccupancy(IntegrationTestCase):
	def counters(self, hostel):
		return frappe.db.get_value(
			"Hostel",
			hostel,
			["total_capacity", "occupied_beds", "vacant_beds", "occupancy_percent"],
			as_dict=True,
		)

	def test_occupancy_math(self):
		hostel = make_hostel("Male")
		double = make_room(hostel.name, capacity=2)
		triple = make_room(hostel.name, capacity=3)

		counters = self.counters(hostel.name)
		self.assertEqual((counters.total_capacity, counters.occupied_beds, counters.vacant_beds), (5, 0, 5))

		allot(make_student("Male").name, beds_of(double.name)[0])
		allot(make_student("Male").name, beds_of(triple.name)[0])
		counters = self.counters(hostel.name)
		self.assertEqual((counters.occupied_beds, counters.vacant_beds), (2, 3))
		self.assertEqual(counters.occupancy_percent, 40)
		self.assertEqual(frappe.db.get_value("Hostel Room", double.name, "status"), "Partially Occupied")

		allot(make_student("Male").name, beds_of(double.name)[1])
		self.assertEqual(frappe.db.get_value("Hostel Room", double.name, "status"), "Full")
		self.assertEqual(frappe.db.get_value("Hostel Room", double.name, "vacant_beds"), 0)

		# Vacant beds in a room under maintenance cannot be allotted, so they are not counted as vacant.
		triple.reload()
		triple.status = "Under Maintenance"
		triple.save()
		counters = self.counters(hostel.name)
		self.assertEqual((counters.total_capacity, counters.occupied_beds, counters.vacant_beds), (5, 3, 0))
		self.assertEqual(frappe.db.get_value("Hostel Room", triple.name, "status"), "Under Maintenance")

		triple.reload()
		triple.status = "Available"
		triple.save()
		self.assertEqual(frappe.db.get_value("Hostel Room", triple.name, "status"), "Partially Occupied")
		self.assertEqual(self.counters(hostel.name).vacant_beds, 2)

	def test_inactive_room_leaves_capacity(self):
		hostel = make_hostel("Female")
		room = make_room(hostel.name, capacity=2)
		make_room(hostel.name, capacity=1)
		room.reload()
		room.is_active = 0
		room.save()
		self.assertEqual(self.counters(hostel.name).total_capacity, 1)

	def test_refresh_heals_tampered_counters(self):
		hostel = make_hostel("Male")
		room = make_room(hostel.name, capacity=2)
		allot(make_student("Male").name, beds_of(room.name)[0])
		frappe.db.set_value("Hostel", hostel.name, {"occupied_beds": 99, "vacant_beds": 0})
		frappe.db.set_value("Hostel Room", room.name, {"occupied_beds": 0, "status": "Full"})
		refresh_all()
		self.assertEqual(self.counters(hostel.name).occupied_beds, 1)
		self.assertEqual(frappe.db.get_value("Hostel Room", room.name, "status"), "Partially Occupied")
		refresh_hostel(hostel.name)
		self.assertEqual(self.counters(hostel.name).vacant_beds, 1)

	def test_gender_change_blocked_by_residents(self):
		hostel = make_hostel("Mixed")
		room = make_room(hostel.name, capacity=2)
		allot(make_student("Female").name, beds_of(room.name)[0])
		hostel.reload()
		hostel.gender = "Male"
		self.assertRaises(frappe.ValidationError, hostel.save)
		hostel.reload()
		hostel.gender = "Female"
		hostel.save()

	def test_hostel_with_residents_cannot_be_deactivated(self):
		hostel = make_hostel("Male")
		room = make_room(hostel.name, capacity=1)
		allot(make_student("Male").name, beds_of(room.name)[0])
		hostel.reload()
		hostel.is_active = 0
		self.assertRaises(frappe.ValidationError, hostel.save)

	def test_hostel_code_is_cleaned(self):
		code = "t" + frappe.generate_hash(length=4)
		hostel = make_hostel("Male", hostel_code=f" {code} ")
		self.assertEqual(hostel.name, code.upper())
