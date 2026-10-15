# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_setup.doctype.hostel_academic_year.hostel_academic_year import academic_year_for_date


def make_year(start_year: int, is_current: int = 0):
	name = f"{start_year}-{start_year + 1}"
	if frappe.db.exists("Hostel Academic Year", name):
		doc = frappe.get_doc("Hostel Academic Year", name)
		if doc.is_current != is_current:
			doc.is_current = is_current
			doc.save()
		return doc
	return frappe.get_doc(
		{
			"doctype": "Hostel Academic Year",
			"academic_year": name,
			"start_date": f"{start_year}-06-01",
			"end_date": f"{start_year + 1}-05-31",
			"is_current": is_current,
		}
	).insert()


class TestHostelAcademicYear(IntegrationTestCase):
	def test_only_one_current_year(self):
		first = make_year(2091, is_current=1)
		self.assertEqual(frappe.db.get_single_value("Hostel Settings", "current_academic_year"), first.name)

		second = make_year(2092, is_current=1)
		self.assertEqual(frappe.db.get_value("Hostel Academic Year", first.name, "is_current"), 0)
		self.assertEqual(frappe.db.count("Hostel Academic Year", {"is_current": 1}), 1)
		self.assertEqual(frappe.db.get_single_value("Hostel Settings", "current_academic_year"), second.name)

	def test_settings_drive_current_flag(self):
		year = make_year(2093)
		settings = frappe.get_single("Hostel Settings")
		settings.current_academic_year = year.name
		settings.save()
		self.assertEqual(frappe.db.get_value("Hostel Academic Year", year.name, "is_current"), 1)
		self.assertEqual(frappe.db.count("Hostel Academic Year", {"is_current": 1}), 1)

	def test_overlap_and_format_rejected(self):
		make_year(2094)
		overlap = frappe.get_doc(
			{
				"doctype": "Hostel Academic Year",
				"academic_year": "2095-2096",
				"start_date": "2095-05-01",
				"end_date": "2096-04-30",
			}
		)
		self.assertRaises(frappe.ValidationError, overlap.insert)

		bad = frappe.get_doc(
			{
				"doctype": "Hostel Academic Year",
				"academic_year": "2097-2099",
				"start_date": "2097-06-01",
				"end_date": "2098-05-31",
			}
		)
		self.assertRaises(frappe.ValidationError, bad.insert)

	def test_june_to_may_window(self):
		self.assertEqual(academic_year_for_date("2026-10-15")[0], "2026-2027")
		self.assertEqual(academic_year_for_date("2027-05-31")[0], "2026-2027")
		self.assertEqual(academic_year_for_date("2027-06-01")[0], "2027-2028")
