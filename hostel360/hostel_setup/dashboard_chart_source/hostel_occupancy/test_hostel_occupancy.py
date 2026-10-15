# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_setup.dashboard_chart_source.hostel_occupancy.hostel_occupancy import BED_STATES, get


class TestHostelOccupancySource(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_one_series_per_bed_state_covering_every_active_bed(self):
		data = get(chart_name="Occupancy by Hostel")
		active_beds = frappe.db.count("Hostel Bed", {"is_active": 1, "hostel": ("is", "set")})

		if not active_beds:
			self.assertEqual(data, {"labels": [], "datasets": []})
			return

		self.assertEqual(data["labels"], sorted(data["labels"]))
		self.assertEqual([series["name"] for series in data["datasets"]], list(BED_STATES))
		for series in data["datasets"]:
			self.assertEqual(len(series["values"]), len(data["labels"]))

		counted = sum(sum(series["values"]) for series in data["datasets"])
		statuses_outside_chart = frappe.db.count(
			"Hostel Bed",
			{"is_active": 1, "hostel": ("is", "set"), "status": ("not in", BED_STATES)},
		)
		self.assertEqual(counted, active_beds - statuses_outside_chart)

	def test_guest_is_refused(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			get(chart_name="Occupancy by Hostel")
