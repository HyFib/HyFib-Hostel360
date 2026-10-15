# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from hostel360.hostel_operations.doctype.ops_backup_record.ops_backup_record import failure_key, human_size
from hostel360.hostel_operations.tests.helpers import (
	DeliveryRecorder,
	clear_settings_cache,
	configure_settings,
)


def make_backup(status="Success", backup_type="Daily", **kwargs):
	return frappe.get_doc(
		{
			"doctype": "Ops Backup Record",
			"backup_type": backup_type,
			"status": status,
			"started_at": add_to_date(now_datetime(), minutes=-12),
			"finished_at": now_datetime(),
			"size_bytes": 3_221_225_472,
			"repository": "s3:objectstorage.ap-mumbai-1/hostel360-backups",
			**kwargs,
		}
	).insert()


class TestOpsBackupRecord(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()

	def setUp(self):
		configure_settings()

	def test_failed_backup_raises_critical_alert_and_success_resolves_it(self):
		with DeliveryRecorder() as recorder:
			failed = make_backup("Failed", message="restic: repository locked")
			alert = frappe.db.get_value(
				"Ops Alert",
				{"open_key": failure_key("Daily")},
				["name", "severity", "category", "description"],
				as_dict=True,
			)
			self.assertTrue(alert, "a failed backup must open an alert")
			self.assertEqual(alert.severity, "Critical")
			self.assertEqual(alert.category, "Backup")
			self.assertIn("repository locked", alert.description)
			self.assertTrue(recorder.texts, "critical backup failures go out by text message")
			self.assertIn(failed.name, frappe.db.get_value("Ops Alert", alert.name, "details"))

			make_backup("Success")
		self.assertEqual(frappe.db.get_value("Ops Alert", alert.name, "status"), "Resolved")

	def test_restore_test_failure_has_its_own_key(self):
		make_backup("Failed", backup_type="Restore Test")
		self.assertTrue(frappe.db.exists("Ops Alert", {"open_key": "backup.restore_test.failed"}))
		make_backup("Success", backup_type="Restore Test")
		self.assertFalse(frappe.db.exists("Ops Alert", {"open_key": "backup.restore_test.failed"}))

	def test_validation_and_derived_fields(self):
		record = make_backup("Success")
		self.assertEqual(record.size_display, "3.0 GB")
		self.assertEqual(record.duration_seconds, 720)
		self.assertRaises(
			frappe.ValidationError,
			make_backup,
			"Success",
			started_at=now_datetime(),
			finished_at=add_to_date(now_datetime(), minutes=-5),
		)

	def test_human_size(self):
		self.assertEqual(human_size(512), "512 B")
		self.assertEqual(human_size(1536), "1.5 KB")
		self.assertEqual(human_size(5 * 1024**4), "5.0 TB")
