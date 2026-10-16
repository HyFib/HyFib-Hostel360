# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.testing import make_block, make_floor, make_hostel


class TestHostelBlock(IntegrationTestCase):
	def test_names_follow_the_hostel(self):
		hostel = make_hostel("Male")
		block = make_block(hostel.name, " a ")
		self.assertEqual(block.name, f"{hostel.name}-A")
		floor = make_floor(block.name, 0)
		self.assertEqual(floor.name, f"{block.name}-F0")
		self.assertEqual(floor.floor_label, "Ground Floor")
		self.assertEqual(floor.hostel, hostel.name)
