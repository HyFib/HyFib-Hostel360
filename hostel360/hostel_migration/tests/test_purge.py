"""Deletion of the institution's data after exit (TR 22) and its certificate.

The deletion runs against this site's real tables inside the test's transaction and is
rolled back afterwards; files, backups, legacy extracts and the export copy live in a
throwaway folder that stands in for the site folder.
"""

import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import frappe
from frappe.query_builder.functions import Count
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, now_datetime
from frappe.utils.password import set_encrypted_password

from hostel360.hostel_migration import certificate
from hostel360.hostel_migration import purge as purge_module
from hostel360.hostel_migration.export import app_doctypes, run_exit_export
from hostel360.hostel_migration.purge import (
	EXPORT_MAX_AGE_DAYS,
	RETAINED_DOCTYPES,
	InstitutionDataPurge,
	PurgeRefused,
	clean_reference,
)
from hostel360.hostel_migration.verify import verify_export

REFERENCE = "ACGCET/HOSTEL/EXIT/2027/014"
KEY = "test-encryption-key-not-used-anywhere-else="


def sample_body(**overrides) -> dict:
	body = {
		"format": certificate.FORMAT,
		"format_version": certificate.FORMAT_VERSION,
		"certificate_id": "H360-DEL-test-localhost-20271101-101500",
		"issued_by": "HyFib Solutions Private Limited",
		"site": "test.localhost",
		"institution": "Test College of Engineering",
		"acknowledgement_reference": REFERENCE,
		"started_at": "2027-11-01 10:15:00",
		"finished_at": "2027-11-01 10:16:30",
		"time_zone": "Asia/Kolkata",
		"operator": {"system_user": "frappe", "host": "app-1", "frappe_user": "Administrator"},
		"export": {
			"export_name": "hostel360-export-test-20271030-090000",
			"exported_on": "2027-10-30 09:00:00",
			"age_days": 2,
			"verified_on": "2027-10-30 11:00:00",
			"verified_by": "Administrator",
			"manifest_sha256": "a" * 64,
			"archive_sha256": "b" * 64,
			"entry_count": 120,
			"total_bytes": 2_500_000,
		},
		"forced_without_export": False,
		"export_problem": None,
		"deleted": {
			"hostel360_doctypes": [
				{"doctype": "Hostel Student", "module": "Hostel People", "kind": "records", "rows": 1500}
			],
			"frappe_records": [{"doctype": "Version", "rows": 9000}],
			"internal_tables": [{"table": "__Auth", "rows": 4}],
			"users": {"count": 4200, "by_kind": {"student": 1500, "parent": 2700}},
			"files": {"records": 1600, "deleted": 1610, "bytes": 900_000_000, "orphans": 10},
			"local_backups": {"files": 3, "bytes": 1000},
			"migration_sources": {"files": 12, "bytes": 2000},
			"export_copies": [{"path": "/srv/exit/hostel360-export-x.tar.gz.h360enc", "result": "deleted"}],
		},
		"totals": {"records": 14504, "files": 1626, "bytes": 900_003_000},
		"verification": {"clean": True, "residual": []},
		"retained": [{"item": "Exit Export Log (1 rows)", "reason": "Record of the exit."}],
		"outstanding": ["Drop the site database."],
	}
	body.update(overrides)
	return body


class TestDeletionCertificate(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.key = certificate.signing_key(KEY)

	def test_signature_detects_any_change(self):
		document = certificate.sign(sample_body(), self.key)
		self.assertEqual(certificate.verify(document, self.key), (True, "The signature is valid."))

		changed = json.loads(json.dumps(document))
		changed["certificate"]["deleted"]["hostel360_doctypes"][0]["rows"] = 1499
		ok, message = certificate.verify(changed, self.key)
		self.assertFalse(ok)
		self.assertIn("altered", message)

		changed = json.loads(json.dumps(document))
		changed["certificate"]["export"]["manifest_sha256"] = "c" * 64
		self.assertFalse(certificate.verify(changed, self.key)[0])

		other = certificate.signing_key(KEY + "x")
		ok, message = certificate.verify(document, other)
		self.assertFalse(ok)
		self.assertIn("different key", message)

		unsigned = {"certificate": document["certificate"]}
		self.assertFalse(certificate.verify(unsigned, self.key)[0])
		self.assertFalse(certificate.verify({"certificate": {"format": "x"}}, self.key)[0])

	def test_signing_key_is_derived_from_the_site_key(self):
		self.assertEqual(certificate.signing_key(KEY), self.key)
		self.assertNotIn(KEY.encode(), self.key)
		self.assertEqual(len(certificate.key_fingerprint(self.key)), 16)
		with self.assertRaises(certificate.CertificateError):
			certificate.signing_key("  ")

	def test_files_markdown_and_reload(self):
		folder = tempfile.mkdtemp(prefix="h360-cert-test-")
		self.addCleanup(shutil.rmtree, folder, True)
		document = certificate.sign(sample_body(), self.key)
		paths = certificate.write(document, folder, pdf=False)

		self.assertIsNone(paths["pdf"])
		for kind in ("json", "markdown"):
			self.assertTrue(os.path.isfile(paths[kind]))
			self.assertEqual(stat.S_IMODE(os.stat(paths[kind]).st_mode), 0o600)
		reloaded = certificate.load(paths["json"])
		self.assertTrue(certificate.verify(reloaded, self.key)[0])

		with open(paths["markdown"], encoding="utf-8") as f:
			text = f.read()
		self.assertIn("# Certificate of Data Deletion: H360-DEL-test-localhost-20271101-101500", text)
		self.assertIn(REFERENCE, text)
		self.assertIn("a" * 64, text)
		self.assertIn("| Hostel Student | records | 1,500 |", text)
		self.assertIn("Drop the site database.", text)
		self.assertIn(document["signature"]["value"], text)

	def test_forced_deletion_is_stated(self):
		body = sample_body(
			export=None, forced_without_export=True, export_problem="No verified exit export is on record."
		)
		text = certificate.render_markdown(certificate.sign(body, self.key))
		self.assertIn("No verified exit export was on record", text)
		self.assertIn("forced without a qualifying export: No verified exit export is on record.", text)


class TestAcknowledgementReference(UnitTestCase):
	def test_reference_rules(self):
		self.assertEqual(clean_reference("  ACGCET/HOSTEL/\n EXIT 14 "), "ACGCET/HOSTEL/ EXIT 14")
		for bad in ("", "  ", "ab", "x" * 141, "REF\x00123"):
			with self.assertRaises(PurgeRefused, msg=repr(bad)):
				clean_reference(bad)


class TestExitExportLog(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.output = tempfile.mkdtemp(prefix="h360-exit-log-test-")
		self.addCleanup(shutil.rmtree, self.output, True)

	def test_export_is_logged_and_verification_recorded(self):
		result = run_exit_export(self.output, include_files=False, archive=True)
		self.assertTrue(result["log"])
		log = frappe.get_doc("Exit Export Log", result["log"])
		self.assertEqual(log.status, "Exported")
		self.assertEqual(log.site, frappe.local.site)
		self.assertEqual(log.manifest_sha256, result["manifest_sha256"])
		self.assertEqual(log.archive_sha256, result["archive_sha256"])
		self.assertFalse(log.include_files)

		from hostel360.hostel_migration.doctype.exit_export_log.exit_export_log import record_verification

		verified = verify_export(result["archive"])
		self.assertTrue(verified.ok, verified.problems)
		self.assertEqual(verified.manifest_sha256, result["manifest_sha256"])
		name, _message = record_verification(verified)
		self.assertEqual(name, log.name)
		log.reload()
		self.assertEqual(log.status, "Verified")
		self.assertTrue(log.verified_on)

		# A copy whose files were changed fails, and the log says so.
		with open(os.path.join(result["directory"], "data", "csv", "Hostel.csv"), "a") as f:
			f.write("tampered\n")
		record_verification(verify_export(result["directory"]))
		log.reload()
		self.assertEqual(log.status, "Verification Failed")
		self.assertIn("data/csv/Hostel.csv", log.verification_problems)

	def test_a_different_manifest_is_never_verified(self):
		result = run_exit_export(self.output, include_files=False, archive=False)
		manifest_path = os.path.join(result["directory"], "manifest.json")
		with open(manifest_path, encoding="utf-8") as f:
			manifest = json.load(f)
		# Drop a file and its manifest entry: internally consistent, but not the export
		# that was made.
		dropped = manifest["entries"].pop()
		manifest["totals"]["entries"] -= 1
		os.remove(os.path.join(result["directory"], dropped["path"]))
		with open(manifest_path, "w", encoding="utf-8") as f:
			json.dump(manifest, f)

		from hostel360.hostel_migration.doctype.exit_export_log.exit_export_log import record_verification

		verified = verify_export(result["directory"])
		self.assertTrue(verified.ok, verified.problems)
		record_verification(verified)
		log = frappe.get_doc("Exit Export Log", result["log"])
		self.assertEqual(log.status, "Verification Failed")
		self.assertIn("manifest.json differs", log.verification_problems)

	def test_unlogged_export_is_adopted_and_other_sites_ignored(self):
		from hostel360.hostel_migration.doctype.exit_export_log.exit_export_log import record_verification

		result = run_exit_export(self.output, include_files=False, archive=False, record=False)
		self.assertIsNone(result["log"])
		verified = verify_export(result["directory"])

		name, message = record_verification(verified, site="another.example.ac.in")
		self.assertIsNone(name)
		self.assertIn("another.example.ac.in", message)

		name, _message = record_verification(verified)
		log = frappe.get_doc("Exit Export Log", name)
		self.assertEqual((log.status, log.recorded_from), ("Verified", "Verification"))
		self.assertEqual(log.export_name, result["name"])


def make_user(roles: list[str]) -> str:
	email = f"purge-{frappe.generate_hash(length=10)}@example.com"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Purge",
			"last_name": "Test",
			"send_welcome_email": 0,
			"user_type": "Website User"
			if set(roles) & {"Hostel Student", "Hostel Parent"}
			else "System User",
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.ignore_password_policy = True
	user.insert(ignore_permissions=True)
	return user.name


def raw_insert(doctype: str, **values) -> str:
	"""Insert without controller hooks (no notifications, scans or e-mails in a test)."""
	doc = frappe.get_doc({"doctype": doctype, "name": frappe.generate_hash(length=12), **values})
	doc.db_insert()
	return doc.name


def row_count(doctype: str) -> int:
	return frappe.qb.from_(frappe.qb.DocType(doctype)).select(Count("*")).run()[0][0]


def hostel360_row_count() -> int:
	return sum(
		row_count(d.name) for d in app_doctypes() if not d.issingle and d.name not in RETAINED_DOCTYPES
	)


@contextmanager
def no_commit():
	"""The purge must not commit inside a test: a commit would make the deletion real."""
	with patch.object(frappe.db, "commit", side_effect=AssertionError("commit during a test purge")):
		yield


def make_export_log(**values) -> str:
	defaults = {
		"doctype": "Exit Export Log",
		"export_name": f"hostel360-export-test-{frappe.generate_hash(length=8)}",
		"status": "Verified",
		"site": frappe.local.site,
		"exported_on": add_days(now_datetime(), -2),
		"exported_by": "Administrator",
		"include_files": 1,
		"manifest_sha256": "a" * 64,
		"verified_on": add_days(now_datetime(), -1),
		"verified_by": "Administrator",
	}
	defaults.update(values)
	return frappe.get_doc(defaults).insert(ignore_permissions=True).name


class PurgeTestCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.root = tempfile.mkdtemp(prefix="h360-purge-test-")
		self.addCleanup(shutil.rmtree, self.root, True)
		self.site_path = os.path.join(self.root, "sites", "site")
		self.certificates = os.path.join(self.root, "certificates")
		os.makedirs(self.site_path)
		# Each test decides which exports exist.
		frappe.db.delete("Exit Export Log")

	def purge(self, **kwargs) -> InstitutionDataPurge:
		kwargs.setdefault("certificate_dir", self.certificates)
		return InstitutionDataPurge(
			frappe.local.site, REFERENCE, site_path=self.site_path, maintenance=False, **kwargs
		)

	def put(self, relative: str, content: bytes = b"data") -> str:
		path = os.path.join(self.site_path, relative)
		os.makedirs(os.path.dirname(path), exist_ok=True)
		with open(path, "wb") as f:
			f.write(content)
		return path


class TestPurgeSafeguards(PurgeTestCase):
	def test_site_must_be_confirmed(self):
		with self.assertRaises(PurgeRefused):
			InstitutionDataPurge(
				"another.example.ac.in", REFERENCE, site_path=self.site_path, maintenance=False
			)
		with self.assertRaises(PurgeRefused):
			InstitutionDataPurge(frappe.local.site, " ", site_path=self.site_path, maintenance=False)

	def test_refused_without_a_verified_export(self):
		make_export_log(status="Exported", verified_on=None)
		with self.assertRaises(PurgeRefused) as raised:
			self.purge().check()
		self.assertIn("No verified exit export", str(raised.exception))

		checks = self.purge(force_without_export=True).check()
		self.assertTrue(checks.forced)
		self.assertIsNone(checks.export)
		self.assertIn("No verified exit export", checks.problem)

	def test_refused_when_the_export_is_too_old(self):
		make_export_log(exported_on=add_days(now_datetime(), -(EXPORT_MAX_AGE_DAYS + 1)))
		with self.assertRaises(PurgeRefused) as raised:
			self.purge().check()
		self.assertIn(f"at most {EXPORT_MAX_AGE_DAYS} days old", str(raised.exception))

	def test_refused_when_the_export_left_out_files(self):
		make_export_log(include_files=0)
		with self.assertRaises(PurgeRefused) as raised:
			self.purge().check()
		self.assertIn("without the uploaded files", str(raised.exception))

	def test_newest_verified_export_counts_and_newer_failures_warn(self):
		make_export_log(exported_on=add_days(now_datetime(), -(EXPORT_MAX_AGE_DAYS + 5)))
		recent = make_export_log(exported_on=add_days(now_datetime(), -3))
		make_export_log(status="Verification Failed", exported_on=add_days(now_datetime(), -1))
		checks = self.purge().check()
		self.assertFalse(checks.forced)
		self.assertEqual(checks.export.name, recent)
		self.assertEqual(len(checks.warnings), 1)

	def test_certificate_may_not_go_inside_the_site_folder(self):
		make_export_log()
		with self.assertRaises(PurgeRefused):
			self.purge(certificate_dir=os.path.join(self.site_path, "private", "files")).check()
		with self.assertRaises(PurgeRefused):
			self.purge(
				certificate_dir=os.path.join(self.site_path, "..", "assets", "x"), force_without_export=True
			).check()

	def test_dry_run_counts_and_changes_nothing(self):
		make_export_log()
		facility = frappe.get_doc(
			{"doctype": "Room Facility", "facility_name": f"Purge Plan {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		photo = self.put("private/files/plan-photo.jpg", b"12345")
		raw_insert(
			"File",
			file_name="plan-photo.jpg",
			file_url="/private/files/plan-photo.jpg",
			is_private=1,
			attached_to_doctype="Room Facility",
			attached_to_name=facility.name,
		)
		job = self.purge()
		plan = job.plan(job.check())

		rows = {r["doctype"]: r["rows"] for r in plan["deleted"]["hostel360_doctypes"]}
		self.assertEqual(rows["Room Facility"], frappe.db.count("Room Facility"))
		self.assertNotIn("Exit Export Log", rows)
		self.assertEqual(plan["deleted"]["files"]["deleted"], 1)
		self.assertEqual(plan["deleted"]["files"]["bytes"], 5)
		self.assertTrue(frappe.db.exists("Room Facility", facility.name))
		self.assertTrue(os.path.exists(photo))
		self.assertFalse(os.path.exists(self.certificates))


class TestPurgeExecution(PurgeTestCase):
	def test_deletes_institution_data_and_issues_a_signed_certificate(self):
		# Never let this test's deletions outlive it, whatever happens below.
		self.addCleanup(frappe.db.rollback)
		rows_before = hostel360_row_count()

		facility = frappe.get_doc(
			{"doctype": "Room Facility", "facility_name": f"Purge Run {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Room Facility",
				"reference_name": facility.name,
				"content": "Fan replaced in room 12",
			}
		).insert(ignore_permissions=True)
		todo = raw_insert(
			"ToDo",
			status="Open",
			allocated_to="Administrator",
			description="Check fan",
			reference_type="Room Facility",
			reference_name=facility.name,
		)
		student = make_user(["Hostel Student"])
		warden = make_user(["Hostel Warden"])
		hyfib = make_user(["HyFib Operations"])
		hyfib_admin = make_user(["HyFib Operations", "Hostel Administrator"])
		notification = raw_insert("Notification Log", for_user=student, subject="Fee due", type="Alert")
		login = raw_insert(
			"Activity Log", user=warden, subject="logged in", operation="Login", status="Success"
		)
		frappe.db.set_single_value("Hostel Settings", "institution_name", "Purge Test College")
		set_encrypted_password(
			"Hostel Payment Settings", "Hostel Payment Settings", "rzp-secret-for-test", "key_secret"
		)

		photo = self.put("private/files/purge-photo.jpg", b"photo")
		unattached = self.put("private/files/purge-upload.pdf", b"%PDF")
		avatar = self.put("public/files/purge-avatar.png", b"avatar")
		orphan = self.put("public/files/nested/purge-orphan.txt", b"orphan")
		kept = self.put("public/files/purge-letterhead.png", b"letterhead")
		theme = self.put("public/files/website_theme/site.css", b"body{}")
		backup = self.put("private/backups/20271030_020000-site-database.sql.gz", b"dump")
		extract = self.put("private/migration/2026-06/students.csv", b"reg_no,name\n")
		files = {
			"photo": raw_insert(
				"File",
				file_url="/private/files/purge-photo.jpg",
				is_private=1,
				attached_to_doctype="Room Facility",
				attached_to_name=facility.name,
			),
			"upload": raw_insert("File", file_url="/private/files/purge-upload.pdf", is_private=1),
			"avatar": raw_insert(
				"File",
				file_url="/files/purge-avatar.png",
				attached_to_doctype="User",
				attached_to_name=student,
			),
			"kept": raw_insert(
				"File",
				file_url="/files/purge-letterhead.png",
				attached_to_doctype="User",
				attached_to_name=hyfib,
			),
		}

		export_dir = os.path.join(self.root, "exit")
		os.makedirs(export_dir)
		archive = os.path.join(export_dir, "hostel360-export-test-20271030-090000.tar.gz.h360enc")
		for path in (archive, archive + ".sha256"):
			with open(path, "wb") as f:
				f.write(b"encrypted export")
		log = make_export_log(archive_path=archive, manifest_sha256="d" * 64, archive_sha256="e" * 64)

		job = self.purge()
		with no_commit():
			result = job.execute(commit=False)

		# Every Hostel360 table is empty, settings and secrets included.
		for d in app_doctypes():
			if d.name in RETAINED_DOCTYPES or d.issingle:
				continue
			self.assertEqual(row_count(d.name), 0, d.name)
		self.assertFalse(
			frappe.db.sql("select count(*) from tabSingles where doctype = 'Hostel Settings'")[0][0]
		)
		self.assertFalse(
			frappe.db.sql("select count(*) from __Auth where doctype = 'Hostel Payment Settings'")[0][0]
		)
		# Frappe records pointing at them, and the people's accounts.
		self.assertFalse(frappe.db.exists("Comment", {"reference_doctype": "Room Facility"}))
		self.assertFalse(frappe.db.exists("ToDo", todo))
		self.assertFalse(frappe.db.exists("Notification Log", notification))
		self.assertFalse(frappe.db.exists("Activity Log", login))
		for user in (student, warden):
			self.assertFalse(frappe.db.exists("User", user))
			self.assertFalse(frappe.db.exists("Has Role", {"parent": user}))
		for user in (hyfib, hyfib_admin, "Administrator"):
			self.assertTrue(frappe.db.exists("User", user))
		for key in ("photo", "upload", "avatar"):
			self.assertFalse(frappe.db.exists("File", files[key]), key)
		self.assertTrue(frappe.db.exists("File", files["kept"]))

		# Files on disk: the data goes; what kept records use and theme stylesheets stay.
		for path in (photo, unattached, avatar, orphan, backup, extract, archive, archive + ".sha256"):
			self.assertFalse(os.path.exists(path), path)
		for path in (kept, theme):
			self.assertTrue(os.path.exists(path), path)
		self.assertTrue(os.path.isdir(os.path.join(self.site_path, "private", "backups")))

		# The certificate: signed, complete, and the export log stamped.
		self.assertTrue(result["clean"], result["residual"])
		body = result["certificate"]["certificate"]
		self.assertEqual(body["acknowledgement_reference"], REFERENCE)
		self.assertEqual(body["institution"], "Purge Test College")
		self.assertEqual(body["export"]["manifest_sha256"], "d" * 64)
		self.assertFalse(body["forced_without_export"])
		rows = {r["doctype"]: r["rows"] for r in body["deleted"]["hostel360_doctypes"]}
		self.assertGreaterEqual(rows["Room Facility"], 1)
		self.assertGreaterEqual(rows["Hostel Settings"], 1)
		related = {r["doctype"]: r["rows"] for r in body["deleted"]["frappe_records"]}
		self.assertGreaterEqual(related["Comment"], 1)
		self.assertGreaterEqual(related["User"], 2)
		self.assertGreaterEqual(body["deleted"]["users"]["count"], 2)
		self.assertEqual(body["deleted"]["files"]["orphans"], 1)
		self.assertEqual(body["deleted"]["local_backups"]["files"], 1)
		self.assertEqual(body["deleted"]["migration_sources"]["files"], 1)
		self.assertEqual({c["result"] for c in body["deleted"]["export_copies"]}, {"deleted"})
		self.assertTrue(body["outstanding"])

		paths = result["paths"]
		self.assertTrue(os.path.isfile(paths["json"]) and os.path.isfile(paths["markdown"]))
		self.assertTrue(paths["pdf"] or paths["pdf_error"])
		self.assertEqual(os.listdir(self.certificates).count(f"{result['certificate_id']}.pending.json"), 0)
		stored = certificate.load(paths["json"])
		self.assertTrue(certificate.verify(stored, certificate.signing_key())[0])
		stored["certificate"]["totals"]["records"] -= 1
		self.assertFalse(certificate.verify(stored, certificate.signing_key())[0])

		log = frappe.get_doc("Exit Export Log", log)
		self.assertEqual(log.certificate_id, result["certificate_id"])
		self.assertEqual(log.acknowledgement_reference, REFERENCE)
		self.assertTrue(log.purged_on and log.export_copy_deleted)

		# Nothing was committed: rolling back brings every row back (background jobs may
		# have added a few in the meantime, never fewer).
		frappe.db.rollback()
		self.assertGreaterEqual(hostel360_row_count(), rows_before)

	def test_keep_export_copy_and_forced_run_are_recorded(self):
		self.addCleanup(frappe.db.rollback)
		export_dir = os.path.join(self.root, "exit")
		folder = os.path.join(export_dir, "hostel360-export-test-20271030-090000")
		os.makedirs(folder)
		make_export_log(directory_path=folder, include_files=0, exported_on=add_days(now_datetime(), -1))
		# A path that h360-export never writes is not touched, whatever the log says.
		make_export_log(archive_path=self.site_path, status="Exported", verified_on=None)

		with no_commit():
			result = self.purge(force_without_export=True, keep_export_copy=True).execute(commit=False)
		body = result["certificate"]["certificate"]
		self.assertTrue(body["forced_without_export"])
		self.assertIn("without the uploaded files", body["export_problem"])
		self.assertTrue(os.path.isdir(folder))
		self.assertTrue(os.path.isdir(self.site_path))
		results = {c["path"]: c["result"] for c in body["deleted"]["export_copies"]}
		self.assertEqual(results[folder], "kept")
		self.assertEqual(results[self.site_path], "skipped: not an export path")
		self.assertIn("Server copy of the exit export", " ".join(r["item"] for r in body["retained"]))

	def test_live_run_retires_the_site_and_commits_once_deleted(self):
		"""The production path: maintenance mode first, a commit after the records, cache cleared."""
		self.addCleanup(frappe.db.rollback)
		make_export_log()
		commit, clear_cache, update_site_config = MagicMock(), MagicMock(), MagicMock()
		with (
			patch.object(frappe.db, "commit", commit),
			patch.object(frappe, "clear_cache", clear_cache),
			patch("frappe.installer.update_site_config", update_site_config),
		):
			job = InstitutionDataPurge(
				frappe.local.site, REFERENCE, site_path=self.site_path, certificate_dir=self.certificates
			)
			result = job.execute()

		self.assertEqual(
			[c.args for c in update_site_config.call_args_list],
			[("maintenance_mode", 1), ("pause_scheduler", 1)],
		)
		self.assertEqual(commit.call_count, 2)
		clear_cache.assert_called_once()
		self.assertEqual(
			result["certificate"]["certificate"]["site_state"], {"maintenance_mode": 1, "scheduler_paused": 1}
		)

	def test_failed_database_step_deletes_nothing(self):
		# Data already on the site is committed; this record stands in for it.
		facility = frappe.get_doc(
			{"doctype": "Room Facility", "facility_name": f"Purge Fail {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: (frappe.delete_doc("Room Facility", facility.name, force=True), frappe.db.commit())
		)
		# Runs before the clean-up above, so nothing else this test does is ever committed.
		self.addCleanup(frappe.db.rollback)
		make_export_log()
		photo = self.put("private/files/fail-photo.jpg")
		rows_before = hostel360_row_count()

		original, calls = purge_module._Op.delete, []

		def delete_then_fail(op):
			calls.append(op.label)
			if len(calls) == 5:
				raise RuntimeError("lost connection to the database")
			return original(op)

		with (
			patch.object(purge_module._Op, "delete", delete_then_fail),
			patch.object(frappe.db, "commit", MagicMock()),
		):
			with self.assertRaises(RuntimeError):
				InstitutionDataPurge(
					frappe.local.site,
					REFERENCE,
					site_path=self.site_path,
					certificate_dir=self.certificates,
					maintenance=False,
				).execute()

		self.assertGreaterEqual(hostel360_row_count(), rows_before)
		self.assertTrue(frappe.db.exists("Room Facility", facility.name))
		self.assertTrue(os.path.exists(photo))
		self.assertEqual(os.listdir(self.certificates), [])

	def test_interrupted_run_is_reported_on_the_next_certificate(self):
		self.addCleanup(frappe.db.rollback)
		make_export_log()
		os.makedirs(self.certificates)
		pending = os.path.join(self.certificates, "H360-DEL-earlier.pending.json")
		with open(pending, "w") as f:
			json.dump(
				{
					"certificate_id": "H360-DEL-earlier",
					"site": frappe.local.site,
					"started_at": "2027-11-01 09:00:00",
					"acknowledgement_reference": REFERENCE,
					"planned": {
						"hostel360_doctypes": [{"doctype": "Hostel Student", "rows": 7}],
						"users": {"count": 3},
					},
				},
				f,
			)
		with no_commit():
			result = self.purge().execute(commit=False)
		runs = result["certificate"]["certificate"]["interrupted_runs"]
		self.assertEqual(
			[(r["certificate_id"], r["planned_records"], r["planned_users"]) for r in runs],
			[("H360-DEL-earlier", 7, 3)],
		)
		self.assertFalse(os.path.exists(pending))


class TestExitCommands(IntegrationTestCase):
	def test_purge_command_refuses_another_site_before_connecting(self):
		from click.testing import CliRunner

		from hostel360.commands import h360_purge_institution_data

		result = CliRunner().invoke(
			h360_purge_institution_data,
			[
				"--confirm-site",
				"another.example.ac.in",
				"--acknowledgement-reference",
				REFERENCE,
				"--execute",
			],
			obj=frappe._dict(sites=[frappe.local.site], profile=False),
		)
		self.assertEqual(result.exit_code, 1)
		self.assertIn("does not match", result.output)

	def test_certificate_can_be_checked_with_a_key_file(self):
		from click.testing import CliRunner

		from hostel360.commands import h360_verify_deletion_certificate

		folder = tempfile.mkdtemp(prefix="h360-cert-cli-test-")
		self.addCleanup(shutil.rmtree, folder, True)
		paths = certificate.write(
			certificate.sign(sample_body(), certificate.signing_key(KEY)), folder, pdf=False
		)
		key_file = os.path.join(folder, "key")
		with open(key_file, "w") as f:
			f.write(KEY + "\n")

		runner = CliRunner()
		good = runner.invoke(
			h360_verify_deletion_certificate,
			["--path", paths["json"], "--key-file", key_file],
			obj=frappe._dict(sites=[], profile=False),
		)
		self.assertEqual(good.exit_code, 0, good.output)
		self.assertIn("The signature is valid.", good.output)

		with open(key_file, "w") as f:
			f.write("some-other-key\n")
		bad = runner.invoke(
			h360_verify_deletion_certificate,
			["--path", paths["json"], "--key-file", key_file],
			obj=frappe._dict(sites=[], profile=False),
		)
		self.assertEqual(bad.exit_code, 1)


class TestMigrationPermissions(UnitTestCase):
	def test_auditor_rows_are_read_only(self):
		"""Design §3: the Institution Auditor only reads (no share, e-mail or changes)."""
		doctypes = frappe.get_all(
			"DocType", filters={"module": "Hostel Migration", "istable": 0}, pluck="name"
		)
		self.assertIn("Exit Export Log", doctypes)
		for doctype in doctypes:
			for perm in frappe.get_meta(doctype).permissions:
				if perm.role != "Institution Auditor":
					continue
				for right in (
					"write",
					"create",
					"delete",
					"submit",
					"cancel",
					"amend",
					"share",
					"email",
					"import",
				):
					self.assertFalse(perm.get(right), f"{doctype}: Auditor has {right}")

	def test_nobody_edits_the_exit_export_log_from_the_desk(self):
		for perm in frappe.get_meta("Exit Export Log").permissions:
			self.assertFalse(perm.get("write") or perm.get("create"), perm.role)
