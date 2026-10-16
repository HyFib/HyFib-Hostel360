# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import json
import os

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.setup.roles import ADMIN_ROLE, OFFICE_ROLE

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "samples")


def sample_mapping() -> str:
	with open(os.path.join(SAMPLES, "sample_mapping.json")) as f:
		return f.read()


def make_run(**values):
	doc = frappe.get_doc(
		{
			"doctype": "Migration Run",
			"title": f"Test {frappe.generate_hash(length=6)}",
			"source_path": "rehearsal",
			"mapping": sample_mapping(),
			**values,
		}
	)
	return doc.insert()


def make_user(role: str) -> str:
	email = f"mig-{frappe.generate_hash(length=8)}@example.com"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Migration",
			"user_type": "System User",
			"send_welcome_email": 0,
			"roles": [{"role": role}],
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	return email


class TestMigrationRun(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_new_run_reads_the_mapping(self):
		run = make_run(status="Completed")
		self.assertEqual(run.status, "Draft")
		self.assertEqual(run.mapping_name, "Legacy hostel register (sample)")
		self.assertEqual(run.source_system, "sample-legacy-register")
		self.assertEqual(len(run.mapping_checksum), 64)
		self.assertEqual(run.dry_run, 1)

	def test_invalid_mapping_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			make_run(mapping="{ not json")
		broken = json.loads(sample_mapping())
		broken["load_order"].remove("rooms")
		with self.assertRaises(frappe.ValidationError):
			make_run(mapping=json.dumps(broken))

	def test_entity_filter_is_checked_and_normalised(self):
		run = make_run(entity_filter="hostels, rooms\n\nstudents ")
		self.assertEqual(run.entity_filter, "hostels\nrooms\nstudents")
		with self.assertRaises(frappe.ValidationError):
			make_run(entity_filter="hostels, wardens")

	def test_started_run_cannot_be_edited(self):
		run = make_run()
		frappe.db.set_value("Migration Run", run.name, "status", "Completed")
		run.reload()
		run.source_path = "another-folder"
		with self.assertRaises(frappe.ValidationError):
			run.save()

	def test_start_checks_folder_and_status(self):
		run = make_run(source_path="/etc")
		with self.assertRaises(frappe.PermissionError):
			run.start()

		frappe.db.set_value("Migration Run", run.name, "status", "Completed")
		run.reload()
		with self.assertRaises(frappe.ValidationError):
			run.start()

	def test_start_queues_a_run_inside_the_migration_folder(self):
		folder = frappe.get_site_path("private", "migration", f"run-{frappe.generate_hash(length=6)}")
		os.makedirs(folder)
		self.addCleanup(os.rmdir, folder)
		run = make_run(source_path=os.path.basename(folder))
		run.start()
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Migration Run", "reference_name": run.name},
			pluck="content",
		)
		self.assertIn("Dry run queued", comments)

	def test_only_administrators_run_migrations(self):
		run = make_run()
		frappe.set_user(make_user(OFFICE_ROLE))
		with self.assertRaises(frappe.PermissionError):
			run.start()
		with self.assertRaises(frappe.PermissionError):
			run.run_again(dry_run=1)

		# An administrator gets past the role check; this run's folder simply does not exist.
		frappe.set_user(make_user(ADMIN_ROLE))
		with self.assertRaises(frappe.ValidationError):
			run.start()

	def test_run_again_copies_the_setup(self):
		run = make_run(entity_filter="hostels")
		frappe.db.set_value("Migration Run", run.name, "status", "Completed with Errors")
		run.reload()
		copy = frappe.get_doc("Migration Run", run.run_again(dry_run=0))
		self.assertEqual(copy.status, "Draft")
		self.assertEqual(copy.dry_run, 0)
		self.assertEqual(
			(copy.mapping, copy.source_path, copy.entity_filter), (run.mapping, run.source_path, "hostels")
		)

	def test_stale_running_run_is_released(self):
		# Nothing holds the engine lock, so a "Running" run is left over from a dead process.
		run = make_run()
		frappe.db.set_value("Migration Run", run.name, "status", "Running")
		run.reload()
		copy = run.run_again(dry_run=1)
		self.assertTrue(frappe.db.exists("Migration Run", copy))
		self.assertEqual(frappe.db.get_value("Migration Run", run.name, "status"), "Failed")
		self.assertIn("stopped before finishing", frappe.db.get_value("Migration Run", run.name, "log"))

	def test_running_run_cannot_be_deleted_or_rerun(self):
		from hostel360.hostel_migration.engine import migration_lock_name

		# The first use of secondary_connection() leaves it active; always return to the
		# primary connection so later tests run where their data is.
		primary = frappe.local.db
		self.addCleanup(setattr, frappe.local, "db", primary)
		run = make_run()
		frappe.db.set_value("Migration Run", run.name, "status", "Running")
		run.reload()
		# Hold the engine lock from a second connection, as a live run would.
		with self.secondary_connection():
			frappe.db.sql("SELECT GET_LOCK(%s, 0)", (migration_lock_name(),))
		try:
			with self.primary_connection():
				with self.assertRaises(frappe.ValidationError):
					run.run_again(dry_run=1)
				with self.assertRaises(frappe.ValidationError):
					frappe.delete_doc("Migration Run", run.name)
				self.assertEqual(frappe.db.get_value("Migration Run", run.name, "status"), "Running")
		finally:
			with self.secondary_connection():
				frappe.db.sql("SELECT RELEASE_LOCK(%s)", (migration_lock_name(),))
