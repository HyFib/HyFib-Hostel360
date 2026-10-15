# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from hostel360.hostel_migration.doctype.migration_record_map.migration_record_map import make_map_key


class UnitTestMigrationRecordMap(UnitTestCase):
	def test_map_key(self):
		self.assertEqual(make_map_key("legacy", "students", " 2021ME014 "), "legacy/students:2021ME014")
		self.assertNotEqual(make_map_key("a", "students", "1"), make_map_key("b", "students", "1"))
		self.assertNotEqual(make_map_key("a", "students", "1"), make_map_key("a", "guardians", "1"))

	def test_long_ids_are_hashed_to_fit(self):
		long_id = "|".join(["2021ME014"] * 30)
		key = make_map_key("legacy", "students", long_id)
		self.assertLessEqual(len(key), 240)
		self.assertTrue(key.startswith("legacy/students:#"))
		self.assertNotEqual(key, make_map_key("legacy", "students", long_id + "x"))


class TestMigrationRecordMap(IntegrationTestCase):
	def make(self, system: str, source_id: str):
		return frappe.get_doc(
			{
				"doctype": "Migration Record Map",
				"source_system": system,
				"source_entity": "hostels",
				"source_id": source_id,
				"target_doctype": "DocType",
				"target_name": "Migration Run",
			}
		).insert(ignore_permissions=True)

	def test_one_record_per_legacy_row(self):
		system = f"test-{frappe.generate_hash(length=6)}"
		first = self.make(system, " H1 ")
		self.assertEqual(first.source_id, "H1")
		self.assertEqual(first.map_key, f"{system}/hostels:H1")
		with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
			self.make(system, "H1")
		# The same legacy ID from another source system is a different row.
		self.make(f"{system}-mess", "H1")

	def test_all_parts_are_required(self):
		with self.assertRaises(frappe.ValidationError):
			self.make("", "H1")
