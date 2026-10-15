"""End-to-end migration runs against the site's real DocTypes."""

import csv
import json
import os
import random
import shutil
import struct
import tempfile
import zlib

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_migration.engine import create_run, execute_run, resolve_source_dir

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")


def tiny_png(rgb=(10, 120, 200)) -> bytes:
	raw = b"".join(b"\x00" + bytes(rgb) * 4 for _ in range(4))

	def chunk(tag, data):
		return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

	header = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
	return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class LegacyExport:
	"""A small legacy export with identifiers unique to one test."""

	def __init__(self):
		self.tag = frappe.generate_hash(length=6).upper()
		self.rng = random.Random(self.tag)
		self.folder = tempfile.mkdtemp(prefix="h360-engine-test-")
		self.hostel_code = f"T{self.tag}"
		self.mobiles = [self.mobile() for _i in range(8)]
		self.files = {
			"hostels.csv": [["id", "code", "name", "type"], ["H1", self.hostel_code.lower(), "TEST HOSTEL", "BOYS"]],
			"rooms.csv": [
				["id", "hostel_id", "room_no", "type", "beds"],
				["R1", "H1", "101", "2 SHARING", "2"],
				["R2", "H1", "102", "3 SHARING", "3"],
			],
			"students.csv": [
				["id", "reg", "name", "sex", "mobile", "city", "photo"],
				["S1", f"t{self.tag}1", "ARUN KUMAR", "M", f"+91 {self.mobiles[0]}", "karaikudi", "photo1.png"],
				["S2", f"T{self.tag}2", "Karthik Raja", "M", self.mobiles[1], "Madurai", ""],
				["S3", f"T{self.tag}3", "Surya", "M", f"0{self.mobiles[2]}", "Theni", ""],
			],
			"allotments.csv": [
				["id", "stud", "room", "bed", "from"],
				["A1", "S1", "R1", "A", "15-06-2025"],
				["A2", "S2", "R1", "B", "15/06/2025"],
				["A3", "S3", "R2", "A", "2025-06-15"],
			],
			"invoices.csv": [
				["inv", "stud", "date", "due", "rent", "electricity", "total", "balance"],
				["I1", "S1", "01-07-2025", "31-07-2025", "Rs. 12,000/-", "2500", "14500", "0"],
				["I2", "S2", "01-07-2025", "31-07-2025", "12000", "", "12000", "2000"],
				["I3", "S3", "01-07-2025", "31-07-2025", "12000", "2500", "14500", "14500"],
			],
			"payments.csv": [
				["rc", "stud", "date", "mode", "amount", "inv"],
				["P1", "S1", "20-07-2025", "UPI", "14,500", "I1"],
				["P2", "S2", "21-07-2025", "CASH", "10000", "I2"],
			],
		}
		self.attachments = {"photo1.png": tiny_png()}
		self.write()

	def mobile(self) -> str:
		return str(self.rng.choice((7, 8, 9))) + "".join(str(self.rng.randrange(10)) for _i in range(9))

	def write(self) -> None:
		for name, rows in self.files.items():
			with open(os.path.join(self.folder, name), "w", newline="", encoding="utf-8") as f:
				csv.writer(f).writerows(rows)
		os.makedirs(os.path.join(self.folder, "attachments"), exist_ok=True)
		for name, content in self.attachments.items():
			with open(os.path.join(self.folder, "attachments", name), "wb") as f:
				f.write(content)

	def set_cell(self, file_name: str, row_id: str, column: str, value: str) -> None:
		rows = self.files[file_name]
		col = rows[0].index(column)
		for row in rows[1:]:
			if row[0] == row_id:
				row[col] = value
		self.write()

	def mapping(self) -> dict:
		lookup = lambda entity, column: {"column": column, "lookup": entity, "required": True}  # noqa: E731
		return {
			"version": 1,
			"name": f"Engine test {self.tag}",
			"source_system": f"engine-test-{self.tag.lower()}",
			"load_order": ["hostels", "rooms", "students", "allotments", "invoices", "payments"],
			"entities": {
				"hostels": {
					"file": "hostels.csv",
					"doctype": "Hostel",
					"key": "id",
					"match_on": ["hostel_code"],
					"fields": {
						"hostel_code": {"column": "code", "transforms": ["upper"], "required": True},
						"hostel_name": {"column": "name", "transforms": ["title"]},
						"gender": {"column": "type", "value_map": {"BOYS": "Male", "GIRLS": "Female"}, "strict": True},
					},
				},
				"rooms": {
					"file": "rooms.csv",
					"doctype": "Hostel Room",
					"key": "id",
					"fields": {
						"hostel": lookup("hostels", "hostel_id"),
						"room_number": {"column": "room_no", "required": True},
						"room_type": {"column": "type", "value_map": {"2 SHARING": "Double", "3 SHARING": "Triple"}, "strict": True},
						"capacity": {"column": "beds", "transforms": ["integer"]},
					},
				},
				"students": {
					"file": "students.csv",
					"doctype": "Hostel Student",
					"key": "id",
					"fields": {
						"register_number": {"column": "reg", "transforms": ["upper"], "required": True},
						"first_name": {"column": "name", "transforms": ["title", {"name": "split_name", "part": "first"}]},
						"last_name": {"column": "name", "transforms": ["title", {"name": "split_name", "part": "last"}]},
						"gender": {"column": "sex", "value_map": {"M": "Male", "F": "Female"}, "strict": True},
						"mobile_number": {"column": "mobile", "transforms": ["mobile"], "required": True},
						"city": {"column": "city", "transforms": ["title"]},
						"validation_status": {"value": "Validated"},
						"photo": {"column": "photo", "attachment": True},
					},
				},
				"allotments": {
					"file": "allotments.csv",
					"doctype": "Room Allotment",
					"key": "id",
					"submit": True,
					"fields": {
						"student": lookup("students", "stud"),
						"room": lookup("rooms", "room"),
						"bed": {"template": "{room}-{bed}", "transforms": ["upper"]},
						"allotment_type": {"value": "New"},
						"from_date": {"column": "from", "transforms": ["date"]},
					},
				},
				"invoices": {
					"file": "invoices.csv",
					"doctype": "Hostel Fee Invoice",
					"key": "inv",
					"submit": True,
					"fields": {
						"student": lookup("students", "stud"),
						"invoice_type": {"value": "Hostel Fee"},
						"posting_date": {"column": "date", "transforms": ["date"]},
						"due_date": {"column": "due", "transforms": ["date"]},
						"migrated_paid_amount": {"value": 0},
					},
					"children": {
						"items": [
							{"$when": "rent", "fee_head": {"value": "Hostel Rent"}, "amount": {"column": "rent", "transforms": ["decimal"]}},
							{"$when": "electricity", "fee_head": {"value": "Electricity Charges"}, "amount": {"column": "electricity", "transforms": ["decimal"]}},
						]
					},
					"reconcile": [
						{"label": "Invoice total", "source_column": "total", "target_field": "total_amount"},
						{"label": "Outstanding", "source_column": "balance", "target_field": "outstanding_amount"},
					],
				},
				"payments": {
					"file": "payments.csv",
					"doctype": "Hostel Payment",
					"key": "rc",
					"submit": True,
					"fields": {
						"student": lookup("students", "stud"),
						"payment_date": {"column": "date", "transforms": ["date"]},
						"mode_of_payment": {"column": "mode", "value_map": {"UPI": "UPI", "CASH": "Cash"}, "strict": True},
						"amount": {"column": "amount", "transforms": ["decimal"]},
					},
					"children": {
						"allocations": [
							{"invoice": {"column": "inv", "lookup": "invoices"}, "allocated_amount": {"column": "amount", "transforms": ["decimal"]}}
						]
					},
					"reconcile": [{"label": "Receipts", "source_column": "amount", "target_field": "amount"}],
				},
			},
		}

	def run(self, dry_run: bool = False, mapping: dict | None = None, entities=None) -> frappe._dict:
		name = create_run(self.folder, mapping or self.mapping(), dry_run=dry_run, entities=entities)
		status = execute_run(name, restrict_source=False)
		doc = frappe.get_doc("Migration Run", name)
		counts = {row.entity: row for row in doc.entities}
		recon = json.loads(doc.reconciliation) if doc.reconciliation else {}
		return frappe._dict(status=status, doc=doc, counts=counts, recon=recon)

	def cleanup(self) -> None:
		shutil.rmtree(self.folder, ignore_errors=True)


class TestMigrationEngine(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.export = LegacyExport()
		self.addCleanup(self.export.cleanup)

	def target(self, entity: str, source_id: str) -> str | None:
		return frappe.db.get_value(
			"Migration Record Map",
			{"source_system": f"engine-test-{self.export.tag.lower()}", "source_entity": entity, "source_id": source_id},
			"target_name",
		)

	def test_full_run_then_rerun_then_change(self):
		first = self.export.run()
		self.assertEqual(first.status, "Completed", first.doc.log)
		self.assertEqual(first.recon["status"], "Matched")
		self.assertEqual(first.counts["students"].created, 3)
		self.assertEqual(first.counts["allotments"].created, 3)

		student = frappe.get_doc("Hostel Student", self.target("students", "S1"))
		self.assertEqual(student.register_number, f"T{self.export.tag}1")
		self.assertEqual((student.first_name, student.last_name, student.city), ("Arun", "Kumar", "Karaikudi"))
		self.assertEqual(student.mobile_number, self.export.mobiles[0])
		self.assertEqual(student.current_bed, f"{self.export.hostel_code}-101-A")
		self.assertEqual(frappe.db.get_value("Hostel Bed", student.current_bed, "status"), "Occupied")

		invoice = frappe.get_doc("Hostel Fee Invoice", self.target("invoices", "I2"))
		self.assertEqual((invoice.total_amount, invoice.paid_amount, invoice.outstanding_amount), (12000, 10000, 2000))
		self.assertEqual([row.fee_head for row in invoice.items], ["Hostel Rent"])
		totals = {t["label"]: t for t in first.recon["totals"]}
		self.assertEqual(totals["Invoice total"]["source_total"], 41000)
		self.assertEqual(totals["Outstanding"]["target_total"], 16500)
		self.assertEqual(totals["Receipts"]["target_total"], 24500)

		second = self.export.run()
		self.assertEqual(second.status, "Completed")
		self.assertEqual(second.doc.total_created + second.doc.total_updated, 0)
		self.assertEqual(second.doc.total_skipped, second.doc.total_source_rows)

		self.export.set_cell("students.csv", "S2", "city", "SIVAKASI")
		third = self.export.run()
		self.assertEqual(third.counts["students"].updated, 1)
		self.assertEqual(third.counts["students"].skipped, 2)
		self.assertEqual(frappe.db.get_value("Hostel Student", self.target("students", "S2"), "city"), "Sivakasi")

	def test_dry_run_keeps_only_the_report(self):
		maps_before = frappe.db.count("Migration Record Map")
		result = self.export.run(dry_run=True)
		self.assertEqual(result.status, "Completed", result.doc.log)
		self.assertEqual(result.recon["status"], "Matched")
		self.assertEqual(result.doc.total_created, 14)
		self.assertFalse(frappe.db.exists("Hostel", self.export.hostel_code))
		self.assertEqual(frappe.db.count("Migration Record Map"), maps_before)
		self.assertTrue(result.doc.report_file and result.doc.report_markdown and result.doc.report_json)
		attached = frappe.get_all(
			"File", filters={"attached_to_doctype": "Migration Run", "attached_to_name": result.doc.name}, pluck="is_private"
		)
		self.assertEqual(attached, [1, 1, 1])
		self.assertEqual(result.recon["attachments"]["checked_dry_run"], 1)

	def test_bad_rows_are_isolated(self):
		self.export.set_cell("students.csv", "S2", "mobile", "12345")
		self.export.set_cell("allotments.csv", "A3", "stud", "S9")
		result = self.export.run()
		self.assertEqual(result.status, "Completed with Errors")
		self.assertEqual(result.recon["status"], "Mismatched")
		self.assertEqual((result.counts["students"].created, result.counts["students"].failed), (2, 1))
		# A2 fails because its student failed, A3 because S9 never existed.
		self.assertEqual((result.counts["allotments"].created, result.counts["allotments"].failed), (1, 2))

		log = result.doc.log
		self.assertIn("students line 3 [S2]: mobile_number:", log)
		self.assertIn("'12345' is not a valid 10-digit Indian mobile number", log)
		self.assertIn("allotments line 4 [A3]: student: legacy students ID 'S9' has not been migrated", log)
		self.assertTrue(self.target("students", "S3"))

		# Fixing the source and running again completes the job without duplicates.
		self.export.set_cell("students.csv", "S2", "mobile", self.export.mobiles[3])
		self.export.set_cell("allotments.csv", "A3", "stud", "S3")
		again = self.export.run()
		self.assertEqual(again.status, "Completed", again.doc.log)
		self.assertEqual(again.counts["students"].created, 1)
		self.assertEqual(again.counts["students"].skipped, 2)
		self.assertEqual(frappe.db.count("Room Allotment", {"student": self.target("students", "S1")}), 1)

	def test_submitted_records_are_not_silently_changed(self):
		self.assertEqual(self.export.run().status, "Completed")
		invoice = self.target("invoices", "I3")
		self.export.set_cell("invoices.csv", "I3", "rent", "13000")
		result = self.export.run()
		self.assertEqual(result.counts["invoices"].failed, 1)
		self.assertIn("is already submitted", result.doc.log)
		self.assertEqual(frappe.db.get_value("Hostel Fee Invoice", invoice, "total_amount"), 14500)

		mapping = self.export.mapping()
		mapping["entities"]["invoices"]["on_submitted_change"] = "skip"
		skipped = self.export.run(mapping=mapping, entities=["invoices"])
		self.assertEqual(skipped.counts["invoices"].failed, 0)
		self.assertEqual(skipped.counts["invoices"].skipped, 3)

	def test_attachment_is_stored_privately_and_verified(self):
		result = self.export.run()
		files = result.recon["attachments"]["files"]
		self.assertEqual(len(files), 1)
		self.assertEqual(files[0]["status"], "verified")
		self.assertEqual(files[0]["source_sha256"], files[0]["target_sha256"])
		photo = frappe.db.get_value("Hostel Student", self.target("students", "S1"), "photo")
		self.assertTrue(photo.startswith("/private/files/"))

		# Rerunning does not store the same photo twice.
		self.export.set_cell("students.csv", "S1", "city", "Devakottai")
		self.export.run()
		count = frappe.db.count(
			"File", {"attached_to_doctype": "Hostel Student", "attached_to_name": self.target("students", "S1")}
		)
		self.assertEqual(count, 1)

		# Unchanged rows still have their stored file checked against the legacy file.
		unchanged = self.export.run()
		self.assertEqual(unchanged.counts["students"].skipped, 3)
		self.assertEqual(unchanged.recon["attachments"]["verified"], 1)
		self.assertEqual(unchanged.doc.attachments_verified, 1)

	def test_missing_attachment_fails_the_row(self):
		self.export.set_cell("students.csv", "S1", "photo", "nowhere.png")
		result = self.export.run(entities=["hostels", "rooms", "students"])
		self.assertEqual(result.counts["students"].failed, 1)
		self.assertIn("nowhere.png was not found", result.doc.log)

	def test_match_on_adopts_existing_records(self):
		frappe.get_doc(
			{"doctype": "Hostel", "hostel_code": self.export.hostel_code, "hostel_name": "Already Here", "gender": "Male"}
		).insert()
		frappe.db.commit()  # nosemgrep
		result = self.export.run(entities=["hostels"])
		self.assertEqual(result.counts["hostels"].updated, 1)
		self.assertEqual(self.target("hostels", "H1"), self.export.hostel_code)
		self.assertEqual(frappe.db.get_value("Hostel", self.export.hostel_code, "hostel_name"), "Test Hostel")

	def test_mapping_that_does_not_fit_the_site_fails_cleanly(self):
		mapping = self.export.mapping()
		mapping["entities"]["rooms"]["fields"]["colour"] = "type"
		result = self.export.run(mapping=mapping)
		self.assertEqual(result.status, "Failed")
		self.assertIn("Hostel Room has no field colour", result.doc.log)
		self.assertFalse(frappe.db.exists("Hostel", self.export.hostel_code))

	def test_missing_column_fails_only_that_entity(self):
		mapping = self.export.mapping()
		mapping["entities"]["rooms"]["fields"]["remarks"] = "remark_column"
		result = self.export.run(mapping=mapping, entities=["hostels", "rooms"])
		self.assertEqual(result.status, "Completed with Errors")
		self.assertEqual(result.counts["rooms"].status, "Failed")
		self.assertEqual(result.counts["hostels"].created, 1)
		self.assertIn("remark_column", result.doc.log)

	def test_desk_runs_read_only_the_migration_folder(self):
		with self.assertRaises(frappe.PermissionError):
			resolve_source_dir(self.export.folder, restrict=True)
		self.assertEqual(resolve_source_dir(self.export.folder, restrict=False), os.path.realpath(self.export.folder))

		inside = frappe.get_site_path("private", "migration", f"test-{self.export.tag}")
		os.makedirs(inside)
		self.addCleanup(shutil.rmtree, inside, True)
		self.assertEqual(resolve_source_dir(f"test-{self.export.tag}", restrict=True), os.path.realpath(inside))
		with self.assertRaises(frappe.PermissionError):
			resolve_source_dir(f"test-{self.export.tag}/../../..", restrict=True)


class TestSampleMigration(IntegrationTestCase):
	def test_sample_dry_run_reconciles(self):
		maps_before = frappe.db.count("Migration Record Map")
		with open(os.path.join(SAMPLES, "sample_mapping.json")) as f:
			mapping = json.load(f)
		name = create_run(SAMPLES, mapping, dry_run=True)
		status = execute_run(name, restrict_source=False)
		doc = frappe.get_doc("Migration Run", name)
		self.assertEqual(status, "Completed", doc.log)
		self.assertEqual(doc.reconciliation_status, "Matched")
		recon = json.loads(doc.reconciliation)
		totals = {t["label"]: t["source_total"] for t in recon["totals"]}
		self.assertEqual(totals["Fee invoices total"], 179000)
		self.assertEqual(totals["Fee invoices outstanding"], 72500)
		self.assertEqual(totals["Mess bills total"], 12371.25)
		self.assertEqual(totals["Receipts total"], 112830.5)
		students = next(e for e in recon["entities"] if e["entity"] == "students")
		self.assertEqual((students["source_rows"], students["excluded"], students["target_records"]), (9, 1, 8))
		self.assertEqual(frappe.db.count("Migration Record Map"), maps_before)
