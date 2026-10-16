# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import glob
import io
import json
import os
import struct
import time
import unicodedata
import zipfile
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import File
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder

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


class FakeRequest:
	"""Just enough of a werkzeug request for the upload checks."""

	def __init__(self, path: str, method: str = "POST", headers: dict | None = None):
		self.path = path
		self.method = method
		self.headers = headers or {}
		# Frappe builds absolute URLs from the request host when there is one.
		self.host = None


PORTAL_UPLOAD_PATH = "/api/method/hostel360.api.portal.upload_attachment"


class UploadPolicyCases:
	"""Policy cases run against both enforcement points (see the two test classes below)."""

	def setUp(self):
		super().setUp()
		patcher = patch.dict(frappe.flags, {"hostel360_enforce_upload_policy": True})
		patcher.start()
		self.addCleanup(patcher.stop)
		self.portal_user = make_user(STUDENT_ROLE)
		self.staff_user = make_user(OFFICE_ROLE)

	def new_file(self, values: dict):
		return frappe.get_doc(values)

	def upload(self, file_name: str, content: bytes, user: str | None = None, portal_channel=True, **values):
		with self.set_user(user or self.portal_user):
			doc = self.new_file({"doctype": "File", "file_name": file_name, "content": content, **values})
			if portal_channel:
				files.mark_portal_upload(doc)
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

		# Frappe parses every PDF for JavaScript when it writes it, so depending on which check
		# runs first a fake PDF is refused there or here; either way nothing may be left on disk.
		pdf_stem = unique()
		with self.assertRaises(Exception):
			self.upload(f"receipt-{pdf_stem}.pdf", b"MZ\x90\x00" + unique().encode() * 20, is_private=1)
		self.assertEqual(files_on_disk(pdf_stem), [])

	def test_rejected_upload_stays_gone_when_the_request_rolls_back(self):
		stem = unique()
		with self.set_user(self.staff_user):
			doc = self.new_file(
				{
					"doctype": "File",
					"file_name": f"tool-{stem}.png",
					"content": b"MZ\x90\x00" + unique().encode() * 20,
					"is_private": 1,
				}
			)
			with self.assertRaises(files.UploadRejected):
				doc.insert(ignore_permissions=True)
		# What Frappe runs for this File when the failed request's transaction rolls back.
		doc.on_rollback()
		self.assertEqual(files_on_disk(stem), [])

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
			doc = self.new_file(
				{
					"doctype": "File",
					"file_name": f"report-{unique()}.json",
					"content": b'{"ok": true}',
					"is_private": 1,
				}
			).insert(ignore_permissions=True)
		self.assertTrue(doc.name)

	def test_portal_files_only_through_the_portal_endpoint(self):
		# /api/resource/File, frappe.client.insert and the like all end in a File insert
		# without the portal's quota, rate limit or clean-up folder.
		stem = unique()
		for path in (
			"/api/resource/File",
			"/api/v2/document/File",
			"/api/method/frappe.client.insert",
			"/api/v2/method/frappe.desk.form.save.savedocs",
			"/api/method/upload_file",
		):
			with (
				patch.object(frappe.local, "request", FakeRequest(path), create=True),
				self.assertRaises(files.PortalUploadBlocked, msg=path),
			):
				self.upload(f"direct-{stem}.pdf", pdf_bytes(), portal_channel=False, is_private=1)
		self.assertEqual(files_on_disk(stem), [])

		with (
			patch.object(frappe.local, "request", FakeRequest("/api/method/upload_file"), create=True),
			self.set_user("Guest"),
			self.assertRaises(files.PortalUploadBlocked),
		):
			doc = self.new_file(
				{"doctype": "File", "file_name": f"guest-{stem}.pdf", "content": pdf_bytes(), "is_private": 1}
			)
			doc.insert(ignore_permissions=True)
		self.assertEqual(files_on_disk(stem), [])

		# Staff uploads through the desk are not affected.
		with patch.object(frappe.local, "request", FakeRequest("/api/method/upload_file"), create=True):
			staff_doc = self.upload(
				f"desk-{unique()}.pdf", pdf_bytes(), user=self.staff_user, portal_channel=False, is_private=1
			)
		self.assertTrue(staff_doc.name)

	def test_portal_endpoint_and_flagged_files_are_accepted(self):
		for path in (PORTAL_UPLOAD_PATH, "/api/v2/method/hostel360.api.portal.upload_attachment"):
			with patch.object(frappe.local, "request", FakeRequest(path), create=True):
				doc = self.upload(
					f"grievance-{unique()}.png", image_bytes("PNG"), portal_channel=False, is_private=1
				)
			self.assertTrue(doc.name)

		# Hostel360 code that applies the portal's rules itself marks the File.
		with patch.object(frappe.local, "request", FakeRequest("/api/resource/File"), create=True):
			self.assertTrue(self.upload(f"flagged-{unique()}.png", image_bytes("PNG"), is_private=1).name)

		# Page requests are not API calls and store nothing a visitor sends.
		with patch.object(frappe.local, "request", FakeRequest("/hostel", "GET"), create=True):
			doc = self.upload(f"page-{unique()}.png", image_bytes("PNG"), portal_channel=False, is_private=1)
		self.assertTrue(doc.name)


class TestUploadValidation(UploadPolicyCases, IntegrationTestCase):
	"""The policy as the File ``before_insert`` hook applies it (bytes already written)."""


class TestUploadValidationBeforeWrite(UploadPolicyCases, IntegrationTestCase):
	"""The same policy applied by the File controller override, before Frappe writes the bytes."""

	def new_file(self, values: dict):
		return files.HostelFile(values)

	def test_rejected_upload_is_never_written(self):
		for name, content in (
			(f"tool-{unique()}.exe", b"MZ\x90\x00"),
			(f"photo-{unique()}.png", b"MZ\x90\x00" + unique().encode() * 20),
			(f"big-{unique()}.pdf", pdf_bytes(5 * MB + 1)),
		):
			with (
				patch.object(files.HostelFile, "save_file", autospec=True) as save_file,
				self.assertRaises(files.UploadRejected, msg=name),
			):
				self.upload(name, content, is_private=1)
			save_file.assert_not_called()

		with (
			patch.object(frappe.local, "request", FakeRequest("/api/resource/File"), create=True),
			patch.object(files.HostelFile, "save_file", autospec=True) as save_file,
			self.assertRaises(files.PortalUploadBlocked),
		):
			self.upload(f"direct-{unique()}.pdf", pdf_bytes(), portal_channel=False)
		save_file.assert_not_called()

	def test_accepted_upload_is_written_once_under_its_clean_name(self):
		stem = unique()
		with patch.object(
			files.HostelFile, "save_file", autospec=True, side_effect=File.save_file
		) as save_file:
			doc = self.upload(f"../{stem}<b>.PDF", pdf_bytes(), is_private=0)
		save_file.assert_called_once()
		self.assertEqual(doc.file_name, f"{stem}_b.pdf")
		self.assertEqual(doc.is_private, 1)
		self.assertEqual(len(files_on_disk(stem)), 1)

	def test_base64_content_is_checked_after_decoding(self):
		import base64

		content = image_bytes("PNG")
		doc = self.upload(
			f"photo-{unique()}.png", base64.b64encode(content).decode(), decode=True, is_private=1
		)
		with open(doc.get_full_path(), "rb") as fh:
			self.assertEqual(fh.read(), content)

		with self.assertRaises(files.UploadRejected):
			self.upload(
				f"fake-{unique()}.png",
				base64.b64encode(b"MZ\x90\x00" * 10).decode(),
				decode=True,
				is_private=1,
			)

	def test_hook_does_not_check_twice(self):
		with patch("hostel360.security.files._checked_name", wraps=files._checked_name) as checked:
			self.upload(f"once-{unique()}.pdf", pdf_bytes(), is_private=1)
		self.assertEqual(checked.call_count, 1)


class TestGenericUploadEndpoints(IntegrationTestCase):
	"""before_request guard for Frappe's own upload and insert endpoints."""

	def setUp(self):
		super().setUp()
		self.portal_user = make_user(STUDENT_ROLE)
		self.staff_user = make_user(OFFICE_ROLE)

	def call(self, path: str, user: str, method: str = "POST", form: dict | None = None, headers=None):
		"""Run the guard as Frappe would for a request with ``user``'s cookie session."""
		request = EnvironBuilder(path=path, method=method, headers=headers or {}).get_request()
		with (
			self.set_user(user),
			patch.object(frappe.local, "request", request, create=True),
			patch.object(frappe.local, "form_dict", frappe._dict(form or {}), create=True),
			# The session's LoginManager: Frappe lets an API key switch only a Guest request.
			patch.object(frappe.local, "login_manager", frappe._dict(user=user), create=True),
		):
			files.block_portal_generic_upload()
			return frappe.session.user

	def assertUploadRefused(self, *args, **kwargs):
		with (
			patch("frappe.handler.upload_file") as handler,
			self.assertRaises((files.PortalUploadBlocked, frappe.AuthenticationError)) as refused,
		):
			self.call(*args, **kwargs)
		handler.assert_not_called()
		return refused.exception

	def with_api_key(self, user: str) -> str:
		doc = frappe.get_doc("User", user)
		doc.api_key = frappe.generate_hash(length=15)
		secret = frappe.generate_hash(length=15)
		doc.api_secret = secret
		doc.save(ignore_permissions=True)
		return f"token {doc.api_key}:{secret}"

	def test_portal_users_and_visitors_are_refused(self):
		paths = (
			"/api/method/upload_file",
			"/api/method/upload_file/anything",
			"/api/v1/method/upload_file",
			"/api/v2/method/upload_file",
			"/api/method/frappe.handler.upload_file",
			"/api/v2/method/frappe.handler.upload_file",
			"/api/resource/File",
			"/api/v1/resource/File",
			"/api/v2/document/File",
		)
		for user in (self.portal_user, "Guest"):
			for path in paths:
				with self.assertRaises(files.PortalUploadBlocked, msg=f"{user} {path}"):
					self.call(path, user)

		# The deprecated "POST / with cmd" form reaches the same handler.
		with self.assertRaises(files.PortalUploadBlocked):
			self.call("/", self.portal_user, form={"cmd": "upload_file"})

	def test_generic_inserts_of_files_are_refused(self):
		file_doc = json.dumps({"doctype": "File", "file_name": "x.pdf", "content": "JVBERi0="})
		for method in ("frappe.client.insert", "frappe.client.save", "frappe.desk.form.save.savedocs"):
			with self.assertRaises(files.PortalUploadBlocked, msg=method):
				self.call(f"/api/method/{method}", self.portal_user, form={"doc": file_doc})
		with self.assertRaises(files.PortalUploadBlocked):
			self.call(
				"/api/method/frappe.client.insert_many",
				self.portal_user,
				form={"docs": json.dumps([{"doctype": "ToDo"}, json.loads(file_doc)])},
			)
		# Other documents are none of this guard's business.
		self.call(
			"/api/method/frappe.client.insert",
			self.portal_user,
			form={"doc": json.dumps({"doctype": "ToDo"})},
		)

	def test_portal_endpoint_staff_and_reads_are_left_alone(self):
		self.call(PORTAL_UPLOAD_PATH, self.portal_user)
		self.call("/api/v2/method/hostel360.api.portal.upload_attachment", self.portal_user)
		self.call("/api/method/upload_file", self.staff_user)
		self.call("/api/resource/File", self.staff_user)
		self.call("/api/resource/File", self.portal_user, method="GET")
		self.call("/api/method/upload_file", self.portal_user, method="OPTIONS")
		self.call("/hostel", "Guest", method="GET")

	def test_an_authorization_header_never_exempts_a_portal_session(self):
		# Frappe ignores these headers (unknown scheme, no colon, unknown token) and the
		# request goes on as the cookie user, so the guard must judge that user.
		chunk = {"chunk_index": 0, "total_chunk_count": 2, "chunk_byte_offset": 0, "total_file_size": 999999}
		for header in ("x y", "token abc", "Bearer bogus", "Basic Zm9v", "nonsense", "a b c"):
			for path in ("/api/method/upload_file", "/api/v2/method/upload_file"):
				error = self.assertUploadRefused(
					path, self.portal_user, form=chunk, headers={"Authorization": header}
				)
				self.assertIsInstance(error, files.PortalUploadBlocked, f"{header} {path}")
		file_doc = json.dumps({"doctype": "File", "file_name": "x.pdf", "content": "JVBERi0="})
		with self.assertRaises(files.PortalUploadBlocked):
			self.call(
				"/api/method/frappe.client.insert",
				self.portal_user,
				form={"doc": file_doc},
				headers={"Authorization": "x y"},
			)

	def test_token_requests_are_judged_by_the_user_they_authenticate(self):
		staff_token = self.with_api_key(self.staff_user)
		portal_token = self.with_api_key(self.portal_user)

		# A staff key used without a session runs as that staff user: allowed.
		self.assertEqual(
			self.call("/api/method/upload_file", "Guest", headers={"Authorization": staff_token}),
			self.staff_user,
		)
		# A portal user's own key is still a portal user.
		error = self.assertUploadRefused(
			"/api/method/upload_file", "Guest", headers={"Authorization": portal_token}
		)
		self.assertIsInstance(error, files.PortalUploadBlocked)
		# Frappe keeps the cookie user when a session sends someone else's key as well.
		error = self.assertUploadRefused(
			"/api/method/upload_file", self.portal_user, headers={"Authorization": staff_token}
		)
		self.assertIsInstance(error, files.PortalUploadBlocked)
		# A token that authenticates nobody fails as it would in Frappe's own check.
		for header in ("token key:secret", "Bearer bogus", "x y"):
			error = self.assertUploadRefused(
				"/api/method/upload_file", "Guest", headers={"Authorization": header}
			)
			self.assertIsInstance(error, frappe.AuthenticationError, header)
		# Visitors with no header at all are refused as before.
		self.assertUploadRefused("/api/method/upload_file", "Guest")

	def test_user_is_resolved_only_for_upload_requests_and_only_once(self):
		with patch("frappe.auth.validate_auth") as validate:
			self.call(
				"/api/method/frappe.client.get_list", self.portal_user, headers={"Authorization": "x y"}
			)
			validate.assert_not_called()
			self.call("/api/method/upload_file", self.staff_user, headers={"Authorization": "x y"})
			validate.assert_called_once()
			# Registered as an auth hook it would already run inside validate_auth.
			validate.reset_mock()
			with patch.object(frappe.local, "hostel360_resolving_user", True, create=True):
				self.call("/api/method/upload_file", self.staff_user)
			validate.assert_not_called()

	def test_chunked_upload_is_refused_before_the_handler_runs(self):
		# Frappe appends each chunk to a temporary file and returns before any File record
		# exists, so the refusal has to come before frappe.handler.upload_file is reached.
		with (
			patch("frappe.handler.upload_file") as handler,
			self.assertRaises(files.PortalUploadBlocked),
		):
			self.call(
				"/api/method/upload_file",
				self.portal_user,
				form={"chunk_index": 1, "total_chunk_count": 99, "chunk_byte_offset": 0},
			)
		handler.assert_not_called()


class TestStaleChunkFiles(IntegrationTestCase):
	"""Clean-up of the temporary files Frappe's chunked upload_file leaves behind."""

	def chunk_file(
		self, is_private: bool, age_seconds: int = 0, prefix: str = files.CHUNK_FILE_PREFIX
	) -> str:
		path = frappe.get_site_path("private" if is_private else "public", "files", f"{prefix}{unique()}.pdf")
		with open(path, "wb") as fh:
			fh.write(pdf_bytes())
		if age_seconds:
			stamp = time.time() - age_seconds
			os.utime(path, (stamp, stamp))
		self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
		return path

	def test_abandoned_chunks_are_removed_and_live_ones_kept(self):
		stale = [self.chunk_file(False, 2 * 3600), self.chunk_file(True, 2 * 3600)]
		live = [self.chunk_file(False, 60), self.chunk_file(True)]
		ordinary = self.chunk_file(False, 2 * 3600, prefix="report-")

		self.assertGreaterEqual(files.delete_stale_chunk_files(), 2)
		for path in stale:
			self.assertFalse(os.path.exists(path), path)
		for path in [*live, ordinary]:
			self.assertTrue(os.path.exists(path), path)
		self.assertEqual(files.delete_stale_chunk_files(), 0)


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
