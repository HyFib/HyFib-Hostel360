# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import base64
import re
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.client import insert as client_insert
from frappe.client import set_value as client_set_value
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from hostel360.setup.roles import DEPUTY_WARDEN_ROLE, OFFICE_ROLE, WARDEN_ROLE
from hostel360.student_services import services, verification
from hostel360.student_services.testing import (
	capture_notifications,
	make_guardian,
	make_hostel,
	make_student,
	make_user,
	run_form_action,
	scope_to_hostel,
	set_hostel_settings,
)
from hostel360.student_services.utils import mask_name

IGNORE_TEST_RECORD_DEPENDENCIES = ["Hostel Student", "Hostel", "Hostel Room", "User"]
DOCTYPE = "Bonafide Certificate Request"
TAMIL_FONTS = ("Noto Serif Tamil", "Noto Sans Tamil", "Lohit Tamil")
FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts", "~/.fonts")


def tamil_font_installed() -> bool:
	"""Whether a Tamil font (Noto or Lohit) is where fontconfig, and so wkhtmltopdf, finds fonts."""
	for folder in FONT_DIRS:
		root = Path(folder).expanduser()
		if root.is_dir() and next(root.rglob("*Tamil*"), None):
			return True
	return False


def offline_pdf(get_pdf):
	"""frappe.utils.pdf.get_pdf without the links to the site's own assets.

	The dev container cannot resolve the site's host name, and wkhtmltopdf would otherwise
	fail fetching the stylesheets.
	"""

	def render(html, *args, **kwargs):
		html = re.sub(r"<link[^>]*>|<script[^>]*>.*?</script>", "", html, flags=re.S)
		return get_pdf(html, *args, **kwargs)

	return render


class TestBonafideCertificateRequest(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_hostel_settings(
			bonafide_validity_days=90,
			bonafide_signatory_name="Dr. A. Selvam",
			bonafide_signatory_designation="Chief Warden",
			portal_url="https://hostel.example.edu",
		)
		self.hostel = make_hostel()
		self.student = make_student(self.hostel)
		self.warden = make_user([WARDEN_ROLE])
		frappe.cache.delete_keys("rl:hostel360:verify-certificate")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.form_dict = frappe._dict()

	def request(self, purpose="Bank Loan", details="Indian Bank, Karaikudi", student=None):
		return services.request_bonafide((student or self.student).name, purpose, details)

	def approved(self, **kwargs):
		doc = frappe.get_doc(DOCTYPE, self.request(**kwargs))
		doc.approve_by(self.warden)
		return doc

	# ------------------------------------------------------------------ requests

	def test_request_copies_student_details(self):
		doc = frappe.get_doc(DOCTYPE, self.request())
		self.assertEqual(doc.status, "Pending")
		self.assertEqual(doc.hostel, self.hostel)
		self.assertEqual(doc.student_name, self.student.full_name)
		self.assertEqual(doc.year_of_study, "2")
		self.assertFalse(doc.certificate_number)

	def test_new_request_tells_the_approvers(self):
		deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(deputy, self.hostel)
		other_deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(other_deputy, make_hostel())
		with capture_notifications() as sent:
			name = self.request()
		recipients = sent.recipients("bonafide_requested")
		self.assertIn(self.warden, recipients)
		self.assertIn(deputy, recipients)
		self.assertNotIn(other_deputy, recipients)
		self.assertNotIn(self.student.user, recipients)
		message = next(m for m in sent if m["event"] == "bonafide_requested")
		self.assertEqual(message["reference_name"], name)
		self.assertEqual(message["context"]["purpose"], "Bank Loan")
		self.assertEqual(message["context"]["hostel"], self.hostel)

		# Deciding does not tell the approvers again.
		with capture_notifications() as sent:
			frappe.get_doc(DOCTYPE, name).approve_by(self.warden)
		self.assertNotIn("bonafide_requested", sent.events())

	def test_duplicate_pending_request_is_refused(self):
		self.request()
		with self.assertRaises(frappe.DuplicateEntryError):
			self.request(details="Another branch")
		self.request(purpose="Passport", details=None)

	def test_other_purpose_needs_details(self):
		with self.assertRaises(frappe.ValidationError):
			self.request(purpose="Other", details="")

	def test_ineligible_students_cannot_request(self):
		for student in (make_student(self.hostel, validation_status="Pending"), make_student(None)):
			with self.assertRaises(frappe.ValidationError):
				self.request(student=student)

	# ------------------------------------------------------------------ decisions

	def test_approval_numbers_and_dates_the_certificate(self):
		with capture_notifications() as sent:
			doc = self.approved()
		year = getdate(nowdate()).year
		self.assertEqual(doc.status, "Approved")
		self.assertRegex(doc.certificate_number, rf"^BC/{year}/\d{{5}}$")
		self.assertEqual(len(doc.verification_code), 16)
		self.assertRegex(doc.verification_code, r"^[A-Za-z0-9_-]{16}$")
		self.assertEqual(getdate(doc.issue_date), getdate(nowdate()))
		self.assertEqual(getdate(doc.valid_till), getdate(add_days(nowdate(), 90)))
		self.assertEqual(doc.approved_by, self.warden)
		self.assertEqual(sent.recipients("bonafide_decided"), {self.student.user})

		second = self.approved(purpose="Scholarship", details="Post-matric")
		first_number = int(doc.certificate_number.rsplit("/", 1)[1])
		self.assertEqual(int(second.certificate_number.rsplit("/", 1)[1]), first_number + 1)
		self.assertNotEqual(second.verification_code, doc.verification_code)

	def test_rejection_needs_a_reason_and_is_final(self):
		doc = frappe.get_doc(DOCTYPE, self.request())
		with self.assertRaises(frappe.ValidationError):
			doc.reject_by("", self.warden)
		doc.reject_by("Fee dues pending; clear them first", self.warden)
		self.assertEqual(doc.status, "Rejected")
		with self.assertRaises(frappe.ValidationError):
			doc.approve_by(self.warden)

	def test_only_hostel_approvers_decide(self):
		doc = frappe.get_doc(DOCTYPE, self.request())
		office = make_user([OFFICE_ROLE])
		other_deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(other_deputy, make_hostel())
		for user in (office, other_deputy, self.student.user):
			with self.assertRaises(frappe.PermissionError):
				doc.approve_by(user)
		deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(deputy, self.hostel)
		doc.approve_by(deputy)
		self.assertEqual(doc.status, "Approved")

	def test_handover_and_locking(self):
		doc = self.approved()
		office = make_user([OFFICE_ROLE])
		doc.mark_issued_by(office)
		self.assertEqual(doc.status, "Issued")
		self.assertEqual(doc.issued_by, office)
		doc.purpose_details = "Changed after issue"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	# ------------------------------------------------------------------ verification

	def test_verification_lookup(self):
		doc = self.approved()
		result = verification.lookup_certificate(doc.verification_code)
		self.assertEqual(result["state"], "valid")
		self.assertEqual(result["certificate_number"], doc.certificate_number)
		self.assertEqual(result["student_name"], "R***** K")
		self.assertNotIn("Rajesh", str(result))

		frappe.db.set_value(DOCTYPE, doc.name, "valid_till", add_days(nowdate(), -1))
		self.assertEqual(verification.lookup_certificate(doc.verification_code)["state"], "expired")

		for code in ("", "short", doc.verification_code.swapcase(), "x" * 16, "'; drop table--1"):
			self.assertEqual(verification.lookup_certificate(code)["state"], "not_found")

	def test_pending_or_rejected_requests_do_not_verify(self):
		doc = frappe.get_doc(DOCTYPE, self.request())
		doc.reject_by("Not eligible", self.warden)
		self.assertEqual(verification.lookup_certificate("A" * 16)["state"], "not_found")

	def test_public_page_context(self):
		from hostel360.www import verify_certificate

		doc = self.approved()
		frappe.set_user("Guest")
		frappe.local.form_dict = frappe._dict(code=f"  {doc.verification_code} ")
		context = verify_certificate.get_context(frappe._dict())
		self.assertEqual(context.result["state"], "valid")
		self.assertFalse(context.rate_limited)
		self.assertEqual(context.code, doc.verification_code)

		frappe.local.form_dict = frappe._dict()
		self.assertIsNone(verify_certificate.get_context(frappe._dict()).result)

	def test_public_page_is_rate_limited(self):
		from hostel360.www import verify_certificate

		frappe.set_user("Guest")
		frappe.local.form_dict = frappe._dict(code="A" * 16)
		with patch.object(verification, "LOOKUP_LIMIT", 2):
			contexts = [verify_certificate.get_context(frappe._dict()) for _attempt in range(3)]
		self.assertEqual([c.rate_limited for c in contexts], [False, False, True])
		self.assertEqual(contexts[-1].http_status_code, 429)
		self.assertIsNone(contexts[-1].result)

	def test_mask_name(self):
		self.assertEqual(mask_name("Rajesh Kannan"), "R***** K")
		self.assertEqual(mask_name("Priya"), "P****")
		self.assertEqual(mask_name("  s  kumar  raja "), "s K R")
		self.assertEqual(mask_name(""), "")

	# ------------------------------------------------------------------ printing

	def test_certificate_print_has_verifiable_qr(self):
		make_guardian(self.student.name, relation="Father")
		doc = self.approved()
		html = frappe.get_print(DOCTYPE, doc.name, print_format="Bonafide Certificate", no_letterhead=1)
		self.assertIn("BONAFIDE CERTIFICATE", html)
		self.assertIn(doc.certificate_number, html)
		self.assertIn("Dr. A. Selvam", html)
		self.assertIn("son of Guardian", html)
		self.assertIn("applying for a bank loan (Indian Bank, Karaikudi)", html)
		self.assertIn("https://hostel.example.edu/verify-certificate", html)

		match = re.search(r'src="data:image/png;base64,([^"]+)"', html)
		self.assertTrue(match, "QR code image missing")
		from PIL import Image

		image = Image.open(BytesIO(base64.b64decode(match.group(1))))
		self.assertEqual(image.format, "PNG")
		self.assertGreaterEqual(image.size[0], 150)

	def test_unapproved_request_prints_no_certificate(self):
		name = self.request()
		html = frappe.get_print(DOCTYPE, name, print_format="Bonafide Certificate", no_letterhead=1)
		self.assertIn("NOT A CERTIFICATE", html)
		self.assertNotIn("data:image/png", html)
		with self.assertRaises(frappe.ValidationError):
			services.get_bonafide_pdf(name)

	def test_portal_pdf_download(self):
		from frappe.utils import pdf as frappe_pdf

		doc = self.approved()
		frappe.set_user(self.student.user)
		with patch("frappe.utils.pdf.get_pdf", side_effect=offline_pdf(frappe_pdf.get_pdf)):
			pdf = services.get_bonafide_pdf(doc.name)
		self.assertTrue(pdf.startswith(b"%PDF"))
		self.assertFalse(frappe.flags.ignore_print_permissions)

	def test_certificate_fonts_fall_back_to_tamil(self):
		"""Latin text keeps the serif face; Tamil names and addresses fall through to a Tamil font
		instead of dropping out of the PDF."""
		tamil = make_student(self.hostel, first_name="மீனாட்சி")
		doc = self.approved(student=tamil)
		html = frappe.get_print(DOCTYPE, doc.name, print_format="Bonafide Certificate", no_letterhead=1)
		self.assertIn("மீனாட்சி", html)

		match = re.search(r"\.bc\s*\{\s*font-family:\s*([^;]+);", html)
		self.assertTrue(match, "certificate font stack missing")
		families = [f.strip().strip('"').strip("'") for f in match.group(1).split(",")]
		self.assertEqual(families[0], "Times New Roman")
		self.assertEqual(families[-1], "serif")
		positions = [families.index(font) for font in TAMIL_FONTS if font in families]
		self.assertTrue(positions, "no Tamil font in the certificate font stack")
		self.assertGreater(min(positions), families.index("Times New Roman"))

	def test_tamil_text_is_embedded_in_the_pdf(self):
		if not tamil_font_installed():
			self.skipTest("No Tamil font is installed for wkhtmltopdf on this machine")
		from frappe.utils import pdf as frappe_pdf
		from pypdf import PdfReader

		tamil = make_student(self.hostel, first_name="மீனாட்சி")
		doc = self.approved(student=tamil)
		with patch("frappe.utils.pdf.get_pdf", side_effect=offline_pdf(frappe_pdf.get_pdf)):
			pdf = services.get_bonafide_pdf(doc.name)
		fonts = set()
		for page in PdfReader(BytesIO(pdf)).pages:
			for font in (page.get("/Resources", {}).get("/Font") or {}).values():
				fonts.add(str(font.get_object().get("/BaseFont", "")))
		self.assertTrue(any("Tamil" in font for font in fonts), f"no Tamil font embedded: {sorted(fonts)}")

	def test_portal_lists(self):
		doc = self.approved()
		rows = services.list_certificates(self.student.name)
		self.assertEqual(rows[0]["name"], doc.name)
		self.assertTrue(rows[0]["can_download"])
		self.assertFalse(rows[0]["is_expired"])

	# ------------------------------------------------------------------ forged decisions

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	FORGED = {
		"status": "Approved",
		"certificate_number": "BC/2026/99999",
		"verification_code": "AAAAAAAAAAAAAAAA",
		"valid_till": "2099-12-31",
		"approved_by": "Administrator",
	}

	def test_office_staff_cannot_approve_by_saving(self):
		name = self.request()
		office = make_user([OFFICE_ROLE])
		with self.assertRaises(frappe.PermissionError):
			self.as_user(office, client_set_value, DOCTYPE, name, self.FORGED)
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "status"), "Pending")
		self.assertEqual(verification.lookup_certificate("AAAAAAAAAAAAAAAA")["state"], "not_found")

		# A rejected request cannot be turned round either
		frappe.get_doc(DOCTYPE, name).reject_by("Fee dues pending", self.warden)
		with self.assertRaises(frappe.PermissionError):
			self.as_user(office, client_set_value, DOCTYPE, name, self.FORGED)
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "status"), "Rejected")

	def test_an_approved_certificate_cannot_be_altered(self):
		doc = self.approved()
		office = make_user([OFFICE_ROLE])
		for values in (
			{"valid_till": "2099-12-31"},
			{"verification_code": "BBBBBBBBBBBBBBBB"},
			{"year_of_study": "4"},
			{"status": "Issued"},
		):
			with self.assertRaises(frappe.PermissionError):
				self.as_user(office, client_set_value, DOCTYPE, doc.name, values)
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, doc.name, ["valid_till", "verification_code", "status"]),
			(getdate(doc.valid_till), doc.verification_code, "Approved"),
		)
		# Handing it over is still an action office staff may take
		self.as_user(office, run_form_action, frappe.get_doc(DOCTYPE, doc.name), "mark_issued")
		self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "issued_by"), office)

	def test_a_new_request_carries_no_decision(self):
		office = make_user([OFFICE_ROLE])
		saved = self.as_user(
			office,
			client_insert,
			{"doctype": DOCTYPE, "student": self.student.name, "purpose": "Passport", **self.FORGED},
		)
		self.assertEqual(saved["status"], "Pending")
		self.assertFalse(saved["verification_code"])
		self.assertFalse(saved["certificate_number"])
		self.assertEqual(verification.lookup_certificate("AAAAAAAAAAAAAAAA")["state"], "not_found")

	def test_form_actions_work_on_the_stored_request(self):
		other = make_student(make_hostel())
		name = self.request(student=other)
		deputy = make_user([DEPUTY_WARDEN_ROLE])
		scope_to_hostel(deputy, self.hostel)
		form_copy = frappe.get_doc(DOCTYPE, name)
		form_copy.hostel = self.hostel
		with self.assertRaises(frappe.PermissionError):
			self.as_user(deputy, run_form_action, form_copy, "approve")
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, ["status", "hostel"]), ("Pending", other.hostel))

		form_copy = frappe.get_doc(DOCTYPE, name)
		self.as_user(self.warden, form_copy.approve)
		self.assertEqual(form_copy.status, "Approved")

	def test_scoped_staff_cannot_move_a_request_into_their_hostel(self):
		other = make_student(make_hostel())
		name = self.request(student=other)
		office = make_user([OFFICE_ROLE])
		scope_to_hostel(office, self.hostel)
		for hostel in (self.hostel, ""):
			with self.assertRaises(frappe.PermissionError):
				self.as_user(office, client_set_value, DOCTYPE, name, {"hostel": hostel})
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "hostel"), other.hostel)
