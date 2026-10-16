# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from hostel360.hostel_migration.doctype.exit_export_log.exit_export_log import last_verified_export


def make_log(**values):
	doc = {
		"doctype": "Exit Export Log",
		"export_name": f"hostel360-export-test-{frappe.generate_hash(length=8)}",
		"status": "Exported",
		"site": frappe.local.site,
		"exported_on": now_datetime(),
		"manifest_sha256": "A" * 64,
	}
	doc.update(values)
	return frappe.get_doc(doc).insert(ignore_permissions=True)


class TestExitExportLog(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.delete("Exit Export Log")

	def test_checksums_are_normalised_and_checked(self):
		log = make_log(archive_sha256=" " + "B" * 64 + " ")
		self.assertEqual(log.manifest_sha256, "a" * 64)
		self.assertEqual(log.archive_sha256, "b" * 64)
		for bad in ("abc", "g" * 64, ""):
			with self.assertRaises(frappe.ValidationError, msg=repr(bad)):
				make_log(manifest_sha256=bad)

	def test_one_row_per_export(self):
		log = make_log()
		with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
			make_log(export_name=log.export_name)

	def test_last_verified_export_is_the_newest_verified_one_of_this_site(self):
		self.assertIsNone(last_verified_export())
		older = make_log(status="Verified", exported_on=add_days(now_datetime(), -10))
		newer = make_log(status="Verified", exported_on=add_days(now_datetime(), -2))
		make_log(status="Verification Failed", exported_on=add_days(now_datetime(), -1))
		make_log(status="Verified", site="another.example.ac.in", exported_on=now_datetime())
		self.assertEqual(last_verified_export().name, newer.name)
		self.assertNotEqual(older.name, newer.name)
