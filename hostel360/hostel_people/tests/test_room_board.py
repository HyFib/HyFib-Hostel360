# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.page.room_board.room_board import allot, get_board, search_vacant_beds, transfer, vacate
from hostel360.hostel_people.testing import beds_of, make_block, make_floor, make_hostel, make_room, make_student


class TestRoomBoard(IntegrationTestCase):
	def setUp(self):
		self.hostel = make_hostel("Male")
		block = make_block(self.hostel.name, "A")
		self.floor = make_floor(block.name, 1)
		self.room_1 = make_room(self.hostel.name, capacity=2, room_number="110", floor=self.floor.name)
		self.room_2 = make_room(self.hostel.name, capacity=3, room_number="12", floor=self.floor.name)

	def test_board_structure_and_actions(self):
		student = make_student("Male")
		allotment = allot(bed=beds_of(self.room_1.name)[0], student=student.name)

		board = get_board(hostel=self.hostel.name)
		self.assertEqual(len(board["hostels"]), 1)
		floor = board["hostels"][0]["blocks"][0]["floors"][0]
		self.assertEqual(floor["floor_label"], "First Floor")
		# Natural order: room 12 before room 110.
		self.assertEqual([room["room_number"] for room in floor["rooms"]], ["12", "110"])
		occupied = [bed for room in floor["rooms"] for bed in room["beds"] if bed["status"] == "Occupied"]
		self.assertEqual(occupied[0]["allotment"], allotment)
		self.assertEqual(occupied[0]["student_name"], student.full_name)
		self.assertEqual(board["summary"]["total"], 5)
		self.assertEqual(board["summary"]["Occupied"], 1)

		new_name = transfer(allotment=allotment, new_bed=beds_of(self.room_2.name)[0])
		self.assertEqual(frappe.db.get_value("Room Allotment", allotment, "status"), "Transferred")
		vacate(allotment=new_name, reason="Discontinued")
		self.assertEqual(frappe.db.get_value("Room Allotment", new_name, "status"), "Vacated")

	def test_vacant_bed_search_follows_gender(self):
		male = make_student("Male")
		female = make_student("Female")
		beds = [row[0] for row in search_vacant_beds("Hostel Bed", "", "name", 0, 50, {"hostel": self.hostel.name, "student": male.name})]
		self.assertEqual(len(beds), 5)
		self.assertEqual(search_vacant_beds("Hostel Bed", "", "name", 0, 50, {"hostel": self.hostel.name, "student": female.name}), [])

		allot(bed=beds_of(self.room_2.name)[0], student=male.name)
		beds = [row[0] for row in search_vacant_beds("Hostel Bed", "-12-", "name", 0, 50, {"hostel": self.hostel.name})]
		self.assertEqual(len(beds), 2)
		self.assertNotIn(beds_of(self.room_2.name)[0], beds)
