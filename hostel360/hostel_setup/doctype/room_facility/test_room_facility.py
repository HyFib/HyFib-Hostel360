# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestRoomFacility(IntegrationTestCase):
	def test_name_whitespace_is_collapsed(self):
		suffix = frappe.generate_hash(length=6)
		doc = frappe.get_doc({"doctype": "Room Facility", "facility_name": f"  Study   Lamp {suffix} "}).insert()
		self.assertEqual(doc.name, f"Study Lamp {suffix}")
