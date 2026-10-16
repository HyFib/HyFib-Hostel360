# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_setup.workspace.test_hostel360_desk import make_user
from hostel360.setup.desk import save_listview_settings
from hostel360.setup.roles import OFFICE_ROLE

DOCTYPE = "Hostel Room"


class TestListViewSettings(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_staff_resizing_a_column_changes_nothing_shared(self):
		frappe.db.delete("List View Settings", {"name": DOCTYPE})
		fields = json.dumps([{"fieldname": "room_number", "label": "Room", "width": 220}])

		frappe.set_user(make_user([OFFICE_ROLE]))
		result = save_listview_settings(DOCTYPE, {"fields": fields}, [])
		self.assertEqual(json.loads(result["listview_settings"]["fields"])[0]["width"], 220)
		self.assertFalse(frappe.db.exists("List View Settings", DOCTYPE))

		frappe.set_user("Administrator")
		save_listview_settings(DOCTYPE, {"fields": fields}, [])
		self.assertTrue(frappe.db.exists("List View Settings", DOCTYPE))
