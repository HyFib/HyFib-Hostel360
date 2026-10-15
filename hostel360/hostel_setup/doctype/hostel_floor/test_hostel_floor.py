# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_people.testing import make_block, make_floor, make_hostel


class TestHostelFloor(IntegrationTestCase):
	def test_floor_label_and_bounds(self):
		block = make_block(make_hostel("Female").name, "B")
		self.assertEqual(make_floor(block.name, 2).floor_label, "Second Floor")
		bad = frappe.get_doc({"doctype": "Hostel Floor", "block": block.name, "floor_number": 99})
		self.assertRaises(frappe.ValidationError, bad.insert)
