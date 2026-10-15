# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import glob
import io
import os
import struct
import unicodedata
import zipfile
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.security import clamav, files
from hostel360.setup.roles import OFFICE_ROLE, STUDENT_ROLE

MB = 1024 * 1024


def unique() -> str:
	return frappe.generate_hash(length=10)


def pdf_bytes(size: int = 0) -> bytes:
	"""A real (pypdf-readable) PDF, padded with an incompressible attachment when ``size`` is given."""
	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=72, height=72)
	writer.add_metadata({"/Title": unique()})
	if size:
		writer.add_attachment("padding.bin", os.urandom(size))
	buffer = io.BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


def image_bytes(fmt: str) -> bytes:
	from PIL import Image

	buffer = io.BytesIO()
	colour = tuple(int(unique()[i : i + 2], 16) for i in (0, 2, 4))
	Image.new("RGB", (6, 6), colour).save(buffer, format=fmt)
	return buffer.getvalue()


def xlsx_bytes(with_macros: bool = False) -> bytes:
	from openpyxl import Workbook

	workbook = Workbook()
	workbook.active["A1"] = "Register Number"
	workbook.active["A2"] = unique()
	buffer = io.BytesIO()
	workbook.save(buffer)
	if not with_macros:
		return buffer.getvalue()
	with zipfile.ZipFile(buffer, "a") as archive:
		archive.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0 macro")
	return buffer.getvalue()


def make_user(*roles: str) -> str:
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": f"files-{unique()}@example.com",
			"first_name": "Upload",
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	return user.name


def files_on_disk(stem: str) -> list[str]:
	pattern = f"*{stem}*"
	return glob.glob(frappe.get_site_path("public", "files", pattern)) + glob.glob(
		frappe.get_site_path("private", "files", pattern)
	)


class TestFileNames(IntegrationTestCase):
	def test_sanitize_file_name(self):
		cases = {
			"../../etc/passwd": "passwd",
			"..\\..\\boot.ini": "boot.ini",
			"invoice.pdf.exe": "invoice_pdf.exe",
			"Fee Receipt (July).PDF": "Fee Receipt (July).pdf",
			"<img src=x onerror=alert(1)>.png": "img src_x onerror_alert(1).png",
			".htaccess": "htaccess",
			"photo\x00.jpg": "photo.jpg",
			"   ": "file",
		}
		# Tamil (and any other script) survives untouched apart from Unicode normalisation.
		tamil = unicodedata.normalize("NFKC", "விடுப்பு கடிதம்.pdf")
		cases[tamil] = tamil
		for raw, expected in cases.items():
			self.assertEqual(files.sanitize_file_name(raw), expected, raw)
		self.assertLessEqual(len(files.sanitize_file_name("a" * 300 + ".pdf")), files.FILENAME_STEM_LIMIT + 4)

	def test_content_detection(self):
		self.assertTrue(files.content_matches("pdf", pdf_bytes()))
		self.assertTrue(files.content_matches("png", image_bytes("PNG")))
		self.assertTrue(files.content_matches("jpg", image_bytes("JPEG")))
		self.assertTrue(files.content_matches("webp", image_bytes("WEBP")))
		# A PNG saved with a .jpg name is still an image.
		self.assertTrue(files.content_matches("jpg", image_bytes("PNG")))
		self.assertTrue(files.content_matches("xlsx", xlsx_bytes()))
		self.assertTrue(files.content_matches("xls", files.OLE_MAGIC + b"\0" * 64))
		self.assertTrue(files.content_matches("csv", "Register Number,Name\n2026BE01,அருண்\n".encode()))
		self.assertTrue(files.content_matches("csv", "Name,City\nJosé,Chennai\n".encode("cp1252")))

		self.assertFalse(files.content_matches("pdf", b"MZ\x90\x00\x03\x00\x00\x00"))
		self.assertFalse(files.content_matches("png", pdf_bytes()))
		self.assertFalse(files.content_matches("xlsx", b"PK\x03\x04not really a zip"))
		self.assertFalse(files.content_matches("csv", b"a,b\n\x00\x01\x02"))
		self.assertFalse(files.content_matches("csv", b"\x7fELF\x02\x01\x01"))
		self.assertTrue(files.xlsx_has_macros(xlsx_bytes(with_macros=True)))
		self.assertEqual(files.detect_kind(b"PK\x03\x04" + b"\0" * 30), "zip")


class TestUploadValidation(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		patcher = patch.dict(frappe.flags, {"hostel360_enforce_upload_policy": True})
		patcher.start()
		self.addCleanup(patcher.stop)
		self.portal_user = make_user(STUDENT_ROLE)
		self.staff_user = make_user(OFFICE_ROLE)

	def upload(self, file_name: str, content: bytes, user: str | None = None, **values):
		with self.set_user(user or self.portal_user):
			doc = frappe.get_doc({"doctype": "File", "file_name": file_name, "content": content, **values})
			doc.insert(ignore_permissions=True)
		return doc

	def test_portal_pdf_is_accepted_and_forced_private(self):
		stem = unique()
		doc = self.upload(f"medical-{stem}.pdf", pdf_bytes(), is_private=0)
		self.assertEqual(doc.is_private, 1)
		self.assertTrue(doc.file_url.startswith("/private/files/"))
		self.assertTrue(os.path.exists(doc.get_full_path()))
		on_disk = files_on_disk(stem)
		self.assertEqual(len(on_disk), 1)
		self.assertIn(os.sep + "private" + os.sep, on_disk[0])

	def test_portal_images_are_accepted(self):
		for fmt, ext in (("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")):
			doc = self.upload(f"photo-{unique()}.{ext}", image_bytes(fmt), is_private=1)
			self.assertTrue(doc.name)

	def test_disguised_file_is_rejected_and_removed(self):
		stem = unique()
		with self.assertRaises(files.UploadRejected):
			self.upload(f"photo-{stem}.png", b"MZ\x90\x00" + unique().encode() * 20, is_private=1)
		self.assertEqual(files_on_disk(stem), [])

		# Frappe parses every PDF for JavaScript before this module runs, so a fake PDF is
		# refused there; either way nothing may be left on disk.
		pdf_stem = unique()
		with self.assertRaises(Exception):
			self.upload(f"receipt-{pdf_stem}.pdf", b"MZ\x90\x00" + unique().encode() * 20, is_private=1)
		self.assertEqual(files_on_disk(pdf_stem), [])

	def test_blocked_extensions(self):
		for name, content in (
			(f"tool-{unique()}.exe", b"MZ\x90\x00"),
			(f"page-{unique()}.html", b"<html><script>alert(1)</script></html>"),
			(f"vector-{unique()}.svg", b"<svg onload=alert(1)></svg>"),
			(f"list-{unique()}.csv", b"a,b\n1,2\n"),
		):
			with self.assertRaises(files.UploadRejected, msg=name):
				self.upload(name, content, is_private=1)

	def test_size_limits(self):
		stem = unique()
		with self.assertRaises(files.UploadRejected):
			self.upload(f"big-{stem}.pdf", pdf_bytes(5 * MB + 1), is_private=1)
		self.assertEqual(files_on_disk(stem), [])

		staff_doc = self.upload(
			f"big-staff-{unique()}.pdf", pdf_bytes(6 * MB), user=self.staff_user, is_private=1
		)
		self.assertTrue(staff_doc.name)

	def test_staff_may_upload_spreadsheets(self):
		xlsx = self.upload(f"students-{unique()}.xlsx", xlsx_bytes(), user=self.staff_user, is_private=1)
		self.assertTrue(xlsx.name)
		csv = self.upload(
			f"students-{unique()}.csv",
			f"Register Number\n{unique()}\n".encode(),
			user=self.staff_user,
			is_private=1,
		)
		self.assertTrue(csv.name)
		with self.assertRaises(files.UploadRejected):
			self.upload(
				f"macro-{unique()}.xlsx", xlsx_bytes(with_macros=True), user=self.staff_user, is_private=1
			)
		with self.assertRaises(files.UploadRejected):
			self.upload(f"fake-{unique()}.xlsx", pdf_bytes(), user=self.staff_user, is_private=1)

	def test_file_names_are_cleaned(self):
		stem = unique()
		doc = self.upload(f"../../{stem}<script>.v2.PDF", pdf_bytes(), is_private=1)
		self.assertEqual(doc.file_name, f"{stem}_script_v2.pdf")
		self.assertIn(f"{stem}_script_v2", doc.file_url)
		self.assertTrue(os.path.exists(doc.get_full_path()))
		self.assertEqual(len(files_on_disk(stem)), 1)

	def test_portal_users_cannot_attach_links(self):
		with self.assertRaises(files.UploadRejected):
			self.upload(f"link-{unique()}.pdf", b"", file_url="https://example.com/file.pdf")

	def test_system_written_files_are_not_policed(self):
		with patch.dict(frappe.flags, {"hostel360_enforce_upload_policy": False}):
			doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"report-{unique()}.json",
					"content": b'{"ok": true}',
					"is_private": 1,
				}
			).insert(ignore_permissions=True)
		self.assertTrue(doc.name)


class FakeSocket:
	def __init__(self, reply: bytes):
		self.reply = reply
		self.sent = b""

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False

	def settimeout(self, timeout):
		pass

	def sendall(self, data):
		self.sent += bytes(data)

	def recv(self, size):
		reply, self.reply = self.reply, b""
		return reply


class TestMalwareScan(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		conf = patch.dict(frappe.conf, {"clamd_host": "clamd.test", "clamd_port": 3310})
		conf.start()
		self.addCleanup(conf.stop)
		self.raise_alert = self.start(patch("hostel360.hostel_operations.alerts.raise_alert"))
		self.resolve_alert = self.start(patch("hostel360.hostel_operations.alerts.resolve_alert"))
		self.addCleanup(self.clear_scanner_state)
		self.clear_scanner_state()

	def start(self, patcher):
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def clear_scanner_state(self):
		frappe.cache.delete(
			frappe.cache.make_key(files.SCANNER_DOWN_KEY),
			frappe.cache.make_key(f"{files.SCANNER_DOWN_KEY}:alerted"),
			frappe.cache.make_key(files.UNSCANNED_SET),
		)

	def stored_file(self, attached_to=None):
		values = {
			"doctype": "File",
			"file_name": f"scan-{unique()}.pdf",
			"content": pdf_bytes(),
			"is_private": 1,
		}
		if attached_to:
			values.update(attached_to_doctype=attached_to.doctype, attached_to_name=attached_to.name)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def test_instream_protocol(self):
		sock = FakeSocket(b"stream: OK\0")
		with patch("hostel360.security.clamav.socket.create_connection", return_value=sock) as connect:
			result = clamav.scan_bytes(b"x" * (clamav.CHUNK_SIZE + 10), "clamd.test", 3310)
		self.assertTrue(result.clean)
		connect.assert_called_once_with(("clamd.test", 3310), timeout=clamav.CONNECT_TIMEOUT)
		self.assertTrue(sock.sent.startswith(b"zINSTREAM\0"))
		self.assertTrue(sock.sent.endswith(struct.pack("!L", 0)))
		first_length = struct.unpack("!L", sock.sent[10:14])[0]
		self.assertEqual(first_length, clamav.CHUNK_SIZE)

	def test_reply_parsing(self):
		self.assertTrue(clamav.parse_reply("stream: OK").clean)
		infected = clamav.parse_reply("stream: Win.Test.EICAR_HDB-1 FOUND")
		self.assertFalse(infected.clean)
		self.assertEqual(infected.signature, "Win.Test.EICAR_HDB-1")
		with self.assertRaises(clamav.ScannerUnavailable):
			clamav.parse_reply("INSTREAM size limit exceeded. ERROR")
		with self.assertRaises(clamav.ScannerUnavailable):
			clamav.parse_reply("")

	def test_clean_file_is_kept(self):
		doc = self.stored_file()
		with patch(
			"hostel360.security.clamav.socket.create_connection", return_value=FakeSocket(b"stream: OK\0")
		):
			self.assertEqual(files.scan_file(doc.name), "clean")
		self.assertTrue(frappe.db.exists("File", doc.name))
		self.raise_alert.assert_not_called()

	def test_infected_file_is_deleted_and_reported(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Malware attachment test"}).insert()
		doc = self.stored_file(attached_to=todo)
		path = doc.get_full_path()
		with patch(
			"hostel360.security.clamav.socket.create_connection",
			return_value=FakeSocket(b"stream: Win.Test.EICAR_HDB-1 FOUND\0"),
		):
			self.assertEqual(files.scan_file(doc.name), "infected")

		self.assertFalse(frappe.db.exists("File", doc.name))
		self.assertFalse(os.path.exists(path))
		self.raise_alert.assert_called_once()
		kwargs = self.raise_alert.call_args.kwargs
		self.assertEqual(kwargs["severity"], "Critical")
		self.assertEqual(kwargs["category"], "Security")
		self.assertEqual(kwargs["details"]["signature"], "Win.Test.EICAR_HDB-1")
		self.assertEqual(kwargs["details"]["attached_to_name"], todo.name)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "ToDo",
					"reference_name": todo.name,
					"comment_type": "Attachment Removed",
				},
			)
		)

	def test_unreachable_scanner_fails_open_and_alerts_once(self):
		first, second = self.stored_file(), self.stored_file()
		with patch(
			"hostel360.security.clamav.socket.create_connection",
			side_effect=ConnectionRefusedError("refused"),
		):
			self.assertEqual(files.scan_file(first.name), "unscanned")
			self.assertEqual(files.scan_file(second.name), "unscanned")

		self.assertTrue(frappe.db.exists("File", first.name))
		self.raise_alert.assert_called_once()
		kwargs = self.raise_alert.call_args.kwargs
		self.assertEqual(kwargs["key"], files.SCANNER_ALERT_KEY)
		self.assertEqual(kwargs["severity"], "Warning")
		self.assertEqual(kwargs["title"], "Malware scanner unreachable")

		# When the scanner is back, the alert is resolved and the backlog is queued.
		with (
			patch(
				"hostel360.security.clamav.socket.create_connection", return_value=FakeSocket(b"stream: OK\0")
			),
			patch("frappe.enqueue") as enqueue,
		):
			self.assertEqual(files.scan_file(first.name), "clean")
		self.resolve_alert.assert_called_once()
		queued = {call.kwargs["file_name"] for call in enqueue.call_args_list}
		self.assertEqual(queued, {first.name, second.name})

	def test_scanning_can_be_switched_off(self):
		doc = self.stored_file()
		with patch.dict(frappe.conf, {"clamd_host": ""}), patch("frappe.enqueue") as enqueue:
			self.assertIsNone(files.scanner_config())
			with patch.dict(frappe.flags, {"hostel360_scan_in_tests": True}):
				files.enqueue_malware_scan(doc)
			self.assertIsNone(files.scan_file(doc.name))
		enqueue.assert_not_called()

	def test_uploads_queue_a_scan(self):
		doc = self.stored_file()
		with patch.dict(frappe.flags, {"hostel360_scan_in_tests": True}), patch("frappe.enqueue") as enqueue:
			files.enqueue_malware_scan(doc)
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.args[0], files.SCAN_METHOD)
		kwargs = enqueue.call_args.kwargs
		self.assertEqual(kwargs["queue"], "short")
		self.assertEqual(kwargs["file_name"], doc.name)
		self.assertTrue(kwargs["enqueue_after_commit"])
		self.assertEqual(kwargs["job_id"], f"hostel360-scan-{doc.name}")
