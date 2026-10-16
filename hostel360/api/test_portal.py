"""Tests for the student/parent portal API and the /hostel page context.

The emphasis is authorization: a student sees only their own records, a parent only their
linked wards, parent-only and student-only endpoints refuse the other kind of user, guests
and desk users are refused, and full features wait for validation and privacy consent.

Every fixture is created here with random values and rolled back when the class finishes.
Where a call would render a PDF or talk to the payment gateway, the module service is
replaced by a stub; the ownership checks under test run before the service is reached.
"""

import io
import json
import os
import random
import re
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, now_datetime, nowdate
from werkzeug.test import EnvironBuilder

from hostel360.api import portal
from hostel360.setup.roles import ADMIN_ROLE, PARENT_ROLE, STUDENT_ROLE
from hostel360.www import hostel as hostel_page

PDF_BYTES = b"%PDF-1.4\n% receipt\n%%EOF\n"
SESSION_KEYS = {
	"user",
	"kind",
	"display_name",
	"mobile_masked",
	"wards",
	"validation_status",
	"profile_confirmed",
	"consent_required",
	"privacy_notice_version",
	"language",
	"settings",
}
WARD_KEYS = {
	"student",
	"full_name",
	"register_number",
	"photo",
	"hostel",
	"hostel_name",
	"room",
	"room_number",
	"bed",
	"bed_label",
	"status",
	"validation_status",
	"payment_enabled",
}
SETTINGS_KEYS = {
	"institution_name",
	"institution_short_name",
	"logo",
	"office_phone",
	"office_email",
	"support_phone",
	"support_email",
	"payment_enabled",
	"mess_reduction_min_days",
	"mess_day_cutoff_hour",
	"leave_requires_parent_approval",
	"time_zone",
	"upload",
}
BOOT_KEYS = {
	"site_name",
	"institution_name",
	"institution_short_name",
	"logo",
	"office_phone",
	"office_email",
	"support_phone",
	"support_email",
	"is_logged_in",
	"kind",
	"lang",
	"otp_email",
	# Only filled on a demonstration site (hostel360.demo); None everywhere else.
	"demo",
}


# ---------------------------------------------------------------------------- fixtures


def uid(length: int = 6) -> str:
	return frappe.generate_hash(length=length).upper()


def unique_mobile() -> str:
	while True:
		mobile = random.choice("6789") + "".join(random.choices("0123456789", k=9))
		if not frappe.db.exists("Hostel Student", {"mobile_number": mobile}) and not frappe.db.exists(
			"Hostel Guardian", {"mobile_number": mobile}
		):
			return mobile


def make_user(roles: list[str], website_user: bool = True) -> str:
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": f"portal-{uid(8).lower()}@example.com",
			"first_name": "Portal",
			"last_name": uid(4),
			"enabled": 1,
			"send_welcome_email": 0,
			"user_type": "Website User" if website_user else "System User",
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.no_welcome_mail = True
	# Many users are created in one transaction; skip Frappe's sign-up throttle.
	in_import, frappe.flags.in_import = frappe.flags.in_import, True
	try:
		user.insert(ignore_permissions=True)
	finally:
		frappe.flags.in_import = in_import
	return user.name


def make_hostel() -> str:
	code = "P" + uid(5)
	return (
		frappe.get_doc(
			{
				"doctype": "Hostel",
				"hostel_name": f"Portal Test Hostel {code}",
				"hostel_code": code,
				"gender": "Male",
				"is_active": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_beds(hostel: str, capacity: int = 4) -> list[str]:
	room = frappe.get_doc(
		{
			"doctype": "Hostel Room",
			"hostel": hostel,
			"room_number": "R" + uid(4),
			"room_type": {1: "Single", 2: "Double", 3: "Triple", 4: "Four Sharing"}.get(
				capacity, "Dormitory"
			),
			"capacity": capacity,
		}
	).insert(ignore_permissions=True)
	return frappe.get_all("Hostel Bed", filters={"room": room.name}, pluck="name", order_by="bed_label asc")


def make_student(
	bed: str | None = None,
	validation_status: str = "Validated",
	consent: bool = True,
	first_name: str = "Arun",
) -> frappe._dict:
	student = frappe.get_doc(
		{
			"doctype": "Hostel Student",
			"register_number": "PT" + uid(8),
			"first_name": first_name,
			"last_name": "Kumar",
			"gender": "Male",
			"year_of_study": "2",
			"mobile_number": unique_mobile(),
			"status": "Active",
		}
	).insert(ignore_permissions=True)

	if bed:
		allotment = frappe.get_doc(
			{
				"doctype": "Room Allotment",
				"student": student.name,
				"bed": bed,
				"allotment_type": "New",
				"from_date": nowdate(),
			}
		)
		allotment.insert(ignore_permissions=True)
		allotment.submit()

	user = make_user([STUDENT_ROLE])
	# Validation and consent belong to the people module's actions; set their outcome directly.
	frappe.db.set_value(
		"Hostel Student",
		student.name,
		{
			"user": user,
			"validation_status": validation_status,
			"privacy_consent_version": privacy_version() if consent else None,
		},
		update_modified=False,
	)
	return frappe._dict(name=student.name, user=user)


def make_guardian(students: list[str], consent: bool = True, can_approve_leave: int = 1) -> frappe._dict:
	guardian = frappe.get_doc(
		{
			"doctype": "Hostel Guardian",
			"guardian_name": f"Parent {uid(4)}",
			"relation": "Father",
			"mobile_number": unique_mobile(),
		}
	).insert(ignore_permissions=True)
	for student in students:
		doc = frappe.get_doc("Hostel Student", student)
		doc.append(
			"guardians",
			{
				"guardian": guardian.name,
				"relation": "Father",
				"is_primary": 0 if doc.guardians else 1,
				"can_approve_leave": can_approve_leave,
			},
		)
		doc.flags.ignore_permissions = True
		doc.save()

	user = make_user([PARENT_ROLE])
	frappe.db.set_value(
		"Hostel Guardian",
		guardian.name,
		{"user": user, "privacy_consent_version": privacy_version() if consent else None},
		update_modified=False,
	)
	return frappe._dict(name=guardian.name, user=user)


def privacy_version() -> str:
	return frappe.db.get_single_value("Hostel Settings", "privacy_notice_version")


def set_hostel_settings(**values) -> None:
	for fieldname, value in values.items():
		frappe.db.set_single_value("Hostel Settings", fieldname, value)
	frappe.clear_document_cache("Hostel Settings", "Hostel Settings")


def leave_data(start_in_hours: float = 24, hours: float = 72) -> dict:
	start = add_to_date(now_datetime(), hours=start_in_hours)
	return {
		"from_datetime": str(start),
		"to_datetime": str(add_to_date(start, hours=hours)),
		"destination": "Madurai",
		"reason": "Family function",
		"contact_during_leave": unique_mobile(),
	}


def make_invoice(student: str, amount: float = 1500) -> str:
	from hostel360.hostel_fees.services import create_invoice

	return create_invoice(
		student, [{"fee_head": "Hostel Rent", "amount": amount}], "Hostel Fee", add_days(nowdate(), 15)
	)


def make_draft_payment(student: str, invoice: str, amount: float = 500) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Hostel Payment",
			"student": student,
			"mode_of_payment": "Cash",
			"payment_date": nowdate(),
			"amount": amount,
			"allocations": [{"invoice": invoice, "allocated_amount": amount}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def make_reduction_row(student: str) -> str:
	"""A bare Mess Reduction Application row: only its owner matters to the checks under test."""
	doc = frappe.new_doc("Mess Reduction Application")
	doc.update(
		{
			"student": student,
			"from_date": add_days(nowdate(), 5),
			"to_date": add_days(nowdate(), 9),
			"number_of_days": 5,
			"reason": "Home Visit",
			"status": "Pending",
		}
	)
	doc.name = "MRA-PT-" + uid(8)
	doc.db_insert()
	return doc.name


def make_notification(user: str, subject: str, link: str | None = "/hostel/fees") -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Notification Log",
			"type": "Alert",
			"for_user": user,
			"subject": subject,
			"description": f"<p>{subject} details</p>",
			"link": link,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def png_bytes() -> bytes:
	# Random padding keeps each upload's content unique, so Frappe does not share one file.
	return b"\x89PNG\r\n\x1a\n" + frappe.generate_hash(length=32).encode() + b"\x00" * 32


def tamil_catalogue():
	"""The Tamil catalogue, compiled first if this checkout has not been built since it changed."""
	from babel.messages.pofile import read_po
	from frappe.gettext.translate import get_mo_path, get_po_path, write_binary

	po_path = get_po_path("hostel360", "ta")
	with open(po_path, "rb") as fh:
		catalog = read_po(fh)
	# `bench build` compiles the catalogue; compile it here too so the tests do not depend on it.
	mo_path = get_mo_path("hostel360", "ta")
	if not mo_path.exists() or po_path.stat().st_mtime > mo_path.stat().st_mtime:
		write_binary("hostel360", catalog, "ta")
	frappe.translate.clear_cache()
	return catalog


@contextmanager
def multipart_request(filename: str, content: bytes, path: str = "/"):
	"""Simulate the HTTP request of an upload from the portal."""
	request = EnvironBuilder(
		path=path, method="POST", data={"file": (io.BytesIO(content), filename)}
	).get_request()
	previous_request = getattr(frappe.local, "request", None)
	previous_ip = getattr(frappe.local, "request_ip", None)
	frappe.local.request = request
	frappe.local.request_ip = "127.0.0.1"
	try:
		yield request
	finally:
		frappe.local.request = previous_request
		frappe.local.request_ip = previous_ip


# ---------------------------------------------------------------------------- tests


class TestPortalAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		if not privacy_version():
			set_hostel_settings(privacy_notice_version="1.0")
		set_hostel_settings(
			student_portal_enabled=1, parent_portal_enabled=1, leave_requires_parent_approval=1
		)

		hostel = make_hostel()
		beds = make_beds(hostel, 4)
		cls.hostel = hostel
		cls.a = make_student(beds[0], first_name="Arun")
		cls.b = make_student(beds[1], first_name="Bala")
		cls.pending = make_student(beds[2], validation_status="Pending", first_name="Chandru")
		cls.parent_a = make_guardian([cls.a.name])
		cls.parent_b = make_guardian([cls.b.name])
		cls.parent_pending = make_guardian([cls.pending.name])
		cls.staff = make_user([ADMIN_ROLE], website_user=False)

		cls.invoice_a = make_invoice(cls.a.name)
		cls.invoice_b = make_invoice(cls.b.name)
		cls.payment_a = make_draft_payment(cls.a.name, cls.invoice_a)
		cls.payment_b = make_draft_payment(cls.b.name, cls.invoice_b)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def assertRefused(self, fn, *args, exc=frappe.PermissionError, **kwargs):
		with self.assertRaises(exc):
			fn(*args, **kwargs)
		frappe.clear_messages()

	# ------------------------------------------------------------------ session

	def test_get_session_shape_for_student(self):
		with self.set_user(self.a.user):
			session = portal.get_session()

		self.assertTrue(SESSION_KEYS.issubset(session), SESSION_KEYS - set(session))
		self.assertTrue(SETTINGS_KEYS.issubset(session["settings"]), SETTINGS_KEYS - set(session["settings"]))
		self.assertEqual(session["user"], self.a.user)
		self.assertEqual(session["kind"], "student")
		self.assertEqual(session["validation_status"], "Validated")
		self.assertFalse(session["consent_required"])
		self.assertEqual(session["privacy_notice_version"], privacy_version())
		self.assertRegex(session["mobile_masked"], r"^\d{2}x{6}\d{2}$")

		self.assertEqual(len(session["wards"]), 1)
		ward = session["wards"][0]
		self.assertTrue(WARD_KEYS.issubset(ward), WARD_KEYS - set(ward))
		self.assertEqual(ward["student"], self.a.name)
		self.assertEqual(ward["hostel"], self.hostel)
		self.assertTrue(ward["room"] and ward["bed"])

	def test_get_session_for_parent_lists_only_linked_wards(self):
		with self.set_user(self.parent_a.user):
			session = portal.get_session()
		self.assertEqual(session["kind"], "parent")
		self.assertEqual([w["student"] for w in session["wards"]], [self.a.name])
		self.assertTrue(session["profile_confirmed"])

	def test_guest_is_refused(self):
		with self.set_user("Guest"):
			for fn in (portal.get_session, portal.get_fees, portal.list_notifications, portal.get_dashboard):
				self.assertRefused(fn)

	def test_desk_user_is_refused(self):
		with self.set_user(self.staff):
			self.assertRefused(portal.get_session)
			self.assertRefused(portal.get_fees, student=self.a.name)

	def test_portal_user_without_record_is_refused(self):
		# The account is no longer linked to a hostel record: the session ends with that
		# reason (the sign-in rule), not with a bare permission error.
		orphan = make_user([STUDENT_ROLE])
		with self.set_user(orphan):
			with self.assertRaises(portal.PortalAccessClosed) as refused:
				portal.get_session()
			self.assertIn("not linked to a hostel record", str(refused.exception))
			frappe.clear_messages()
			self.assertRefused(portal.list_notifications, exc=portal.PortalAccessClosed)

		orphan_parent = make_user([PARENT_ROLE])
		with self.set_user(orphan_parent):
			self.assertRefused(portal.get_session, exc=portal.PortalAccessClosed)

	def test_switched_off_portal_refuses_existing_sessions(self):
		set_hostel_settings(student_portal_enabled=0)
		try:
			with self.set_user(self.a.user):
				with self.assertRaises(portal.PortalAccessClosed) as refused:
					portal.get_session()
				self.assertIn("not available right now", str(refused.exception))
				frappe.clear_messages()
			with self.set_user(self.parent_a.user):
				self.assertTrue(portal.get_session()["wards"])
		finally:
			set_hostel_settings(student_portal_enabled=1)

	# ------------------------------------------------------------------ student isolation

	def test_student_cannot_read_another_students_lists(self):
		with self.set_user(self.a.user):
			for fn in (
				portal.get_fees,
				portal.list_leaves,
				portal.list_grievances,
				portal.list_certificates,
				portal.get_profile,
				portal.get_dashboard,
				portal.get_menu,
				portal.get_mess_bills,
				portal.list_mess_reductions,
				portal.download_fee_structure,
			):
				self.assertRefused(fn, student=self.b.name)

	def test_student_cannot_open_another_students_documents(self):
		from hostel360.student_services import services as student_services

		grievance_b = student_services.raise_grievance(
			self.b.name,
			{"category": "Plumbing", "subject": "Tap leaking", "description": "Bathroom tap leaks."},
			"Administrator",
		)
		leave_b = student_services.apply_leave(self.b.name, leave_data(), "Warden", "Administrator")
		bonafide_b = student_services.request_bonafide(self.b.name, "Bank Loan", "Education loan")
		reduction_b = make_reduction_row(self.b.name)

		with (
			patch("hostel360.hostel_fees.services.get_receipt_pdf", return_value=PDF_BYTES) as receipt,
			patch("hostel360.student_services.services.get_bonafide_pdf", return_value=PDF_BYTES) as bonafide,
			self.set_user(self.a.user),
		):
			self.assertRefused(portal.get_grievance, name=grievance_b)
			self.assertRefused(portal.comment_grievance, name=grievance_b, comment="Me too")
			self.assertRefused(portal.reopen_grievance, name=grievance_b, reason="Still broken")
			self.assertRefused(portal.rate_grievance, name=grievance_b, rating=5)
			self.assertRefused(portal.cancel_leave, name=leave_b)
			self.assertRefused(portal.cancel_mess_reduction, name=reduction_b)
			self.assertRefused(portal.download_receipt, payment=self.payment_b)
			self.assertRefused(portal.download_bonafide, name=bonafide_b)
			receipt.assert_not_called()
			bonafide.assert_not_called()

			self.assertRefused(portal.get_grievance, name="GRV-NO-SUCH", exc=frappe.DoesNotExistError)

	def test_student_lists_only_their_own_records(self):
		from hostel360.student_services import services as student_services

		mine = student_services.raise_grievance(
			self.a.name,
			{"category": "Electrical", "subject": "Fan not working", "description": "Ceiling fan is dead."},
			"Administrator",
		)
		student_services.raise_grievance(
			self.b.name,
			{"category": "Electrical", "subject": "Light flickers", "description": "Tube light flickers."},
			"Administrator",
		)
		with self.set_user(self.a.user):
			grievances = portal.list_grievances()
			fees = portal.get_fees()
		self.assertIn(mine, [g["name"] for g in grievances])
		owners = set(
			frappe.get_all(
				"Hostel Grievance", filters={"name": ("in", [g["name"] for g in grievances])}, pluck="student"
			)
		)
		self.assertEqual(owners, {self.a.name})
		self.assertEqual(fees["student"], self.a.name)
		self.assertIn(self.invoice_a, [i["name"] for i in fees["invoices"]])
		self.assertNotIn(self.invoice_b, [i["name"] for i in fees["invoices"]])

	def test_receipt_download_for_owner(self):
		with (
			patch("hostel360.hostel_fees.services.get_receipt_pdf", return_value=PDF_BYTES) as receipt,
			self.set_user(self.a.user),
		):
			portal.download_receipt(payment=self.payment_a)
		receipt.assert_called_once_with(self.payment_a)
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertEqual(frappe.local.response.filecontent, PDF_BYTES)
		self.assertTrue(frappe.local.response.filename.endswith(".pdf"))
		self.assertNotIn("/", frappe.local.response.filename)
		for key in ("type", "filecontent", "filename"):
			frappe.local.response.pop(key, None)

	# ------------------------------------------------------------------ parents

	def test_parent_reaches_only_linked_wards(self):
		with (
			patch("hostel360.hostel_fees.services.get_receipt_pdf", return_value=PDF_BYTES),
			self.set_user(self.parent_a.user),
		):
			self.assertEqual(portal.get_fees(student=self.a.name)["student"], self.a.name)
			# A single ward is chosen automatically.
			self.assertEqual(portal.get_fees()["student"], self.a.name)
			self.assertRefused(portal.get_fees, student=self.b.name)
			self.assertRefused(portal.list_leaves, student=self.b.name)
			self.assertRefused(portal.get_profile, student=self.b.name)
			self.assertRefused(portal.download_receipt, payment=self.payment_b)
			portal.download_receipt(payment=self.payment_a)
			self.assertEqual(frappe.local.response.type, "pdf")
		for key in ("type", "filecontent", "filename"):
			frappe.local.response.pop(key, None)

	def test_parent_with_several_wards_names_the_student_for_writes(self):
		hostel = make_hostel()
		beds = make_beds(hostel, 2)
		first, second = make_student(beds[0]), make_student(beds[1])
		parent = make_guardian([first.name, second.name])

		with (
			patch("hostel360.student_services.services.request_bonafide", return_value="BON-TEST") as request,
			self.set_user(parent.user),
		):
			self.assertEqual({w["student"] for w in portal.get_session()["wards"]}, {first.name, second.name})
			self.assertRefused(portal.request_bonafide, purpose="Bank Loan", exc=frappe.ValidationError)
			request.assert_not_called()
			self.assertEqual(
				portal.request_bonafide(purpose="Bank Loan", student=second.name), {"name": "BON-TEST"}
			)
		self.assertEqual(request.call_args.args[0], second.name)

	def test_parent_only_endpoints_refuse_students(self):
		with self.set_user(self.a.user):
			self.assertRefused(portal.pending_leave_approvals)
			self.assertRefused(portal.decide_leave, name="LV-ANY", action="approve")

	def test_student_only_endpoints_refuse_parents(self):
		with self.set_user(self.parent_a.user):
			self.assertRefused(portal.confirm_profile, updates={"city": "Karaikudi"})
			self.assertRefused(portal.submit_feedback, feedback_type="Mess", overall=4)

	def test_parent_approves_own_wards_leave_only(self):
		with self.set_user(self.a.user):
			result = portal.apply_leave(**leave_data(start_in_hours=200), apply_mess_reduction=False)
		self.assertEqual(result["status"], "Pending Parent Approval")

		with self.set_user(self.parent_b.user):
			self.assertNotIn(result["name"], [row["name"] for row in portal.pending_leave_approvals()])
			self.assertRefused(portal.decide_leave, name=result["name"], action="approve")

		with self.set_user(self.parent_a.user):
			self.assertIn(result["name"], [row["name"] for row in portal.pending_leave_approvals()])
			self.assertRefused(
				portal.decide_leave, name=result["name"], action="maybe", exc=frappe.ValidationError
			)
			decision = portal.decide_leave(name=result["name"], action="approve", remarks="Okay")
		self.assertEqual(decision, {"ok": True, "status": "Pending Warden Approval"})

	def test_parent_does_not_see_students_confidential_grievance(self):
		with self.set_user(self.a.user):
			confidential = portal.raise_grievance(
				category="Ragging or Harassment",
				subject="Seniors troubling juniors",
				description="Happens every night in block A.",
			)["name"]
			open_one = portal.raise_grievance(
				category="Water", subject="No hot water", description="Geyser not working."
			)["name"]
			self.assertIn(confidential, [g["name"] for g in portal.list_grievances()])

		with self.set_user(self.parent_a.user):
			visible = [g["name"] for g in portal.list_grievances()]
			self.assertIn(open_one, visible)
			self.assertNotIn(confidential, visible)
			self.assertRefused(portal.get_grievance, name=confidential)
			self.assertEqual(portal.get_grievance(name=open_one)["name"], open_one)
			# Rating belongs to the student or whoever raised the grievance.
			self.assertRefused(portal.rate_grievance, name=open_one, rating=4)

	# ------------------------------------------------------------------ validation and consent

	def test_session_of_an_unvalidated_record_is_refused_with_its_reason(self):
		# Sign-in is only for validated residents (TR 3.1, 4.1); a session opened earlier must
		# stop working too, and say why instead of failing like an expired session.
		with self.set_user(self.pending.user):
			for fn in (portal.get_session, portal.get_profile, portal.list_notifications, portal.get_fees):
				with self.assertRaises(portal.PortalAccessClosed) as refused:
					fn()
				self.assertIn("validated", str(refused.exception))
				frappe.clear_messages()
			self.assertRefused(
				portal.accept_privacy_notice, version=privacy_version(), exc=portal.PortalAccessClosed
			)
			self.assertRefused(
				portal.confirm_profile, updates={"city": "Karaikudi"}, exc=portal.PortalAccessClosed
			)

		# A guardian whose only ward is not validated yet is held back the same way.
		with self.set_user(self.parent_pending.user):
			self.assertRefused(portal.get_session, exc=portal.PortalAccessClosed)

	def test_session_ends_when_the_record_is_closed_or_rejected(self):
		hostel = make_hostel()
		beds = make_beds(hostel, 2)
		leaver, rejected = make_student(beds[0]), make_student(beds[1])
		parent = make_guardian([leaver.name])
		with self.set_user(leaver.user):
			self.assertEqual(portal.get_session()["kind"], "student")

		frappe.db.set_value("Hostel Student", leaver.name, "status", "Vacated")
		frappe.db.set_value("Hostel Student", rejected.name, "validation_status", "Rejected")
		with self.set_user(leaver.user):
			self.assertRefused(portal.get_session, exc=portal.PortalAccessClosed)
			self.assertRefused(portal.get_fees, exc=portal.PortalAccessClosed)
		with self.set_user(rejected.user):
			self.assertRefused(portal.get_session, exc=portal.PortalAccessClosed)
		with self.set_user(parent.user):
			# Their only ward has left the hostel, so the parent portal is closed as well.
			self.assertRefused(portal.get_session, exc=portal.PortalAccessClosed)

	def test_validated_student_confirms_details_on_first_sign_in(self):
		student = make_student(make_beds(make_hostel(), 1)[0])
		with self.set_user(student.user):
			self.assertFalse(portal.get_session()["profile_confirmed"])
			self.assertEqual(portal.confirm_profile(updates={"city": "Karaikudi"}), {"ok": True})
			self.assertTrue(portal.get_session()["profile_confirmed"])

	def test_parent_with_a_pending_ward_is_held_back_for_that_ward_only(self):
		hostel = make_hostel()
		beds = make_beds(hostel, 2)
		first, second = sorted([make_student(beds[0]), make_student(beds[1])], key=lambda s: s.name)
		# The ward a parent's request falls back to (the first by register number) is the pending one.
		frappe.db.set_value("Hostel Student", first.name, "validation_status", "Pending")
		parent = make_guardian([first.name, second.name])

		with self.set_user(parent.user):
			wards = {ward["student"]: ward for ward in portal.get_session()["wards"]}
			self.assertEqual(wards[first.name]["validation_status"], "Pending")
			self.assertEqual(wards[second.name]["validation_status"], "Validated")
			self.assertEqual(portal.get_fees(student=second.name)["student"], second.name)
			self.assertRefused(portal.get_fees, student=first.name, exc=portal.PortalValidationPending)

			# An upload is checked against the ward the grievance is for, not the first ward.
			with multipart_request("leak.png", png_bytes()):
				self.assertTrue(
					portal.upload_attachment(purpose="grievance", student=second.name)["file_url"]
				)
			with multipart_request("leak.png", png_bytes()):
				self.assertRefused(
					portal.upload_attachment, purpose="grievance", exc=portal.PortalValidationPending
				)

	def test_inactive_student_keeps_dues_but_not_resident_features(self):
		student = make_student(make_beds(make_hostel(), 1)[0])
		invoice = make_invoice(student.name)
		frappe.db.set_value("Hostel Student", student.name, "status", "Suspended")
		order = {"transaction": "PTX-TEST", "gateway": "razorpay", "checkout": {}}

		with (
			patch("hostel360.hostel_fees.services.create_payment_order", return_value=order) as create,
			self.set_user(student.user),
		):
			self.assertIn(invoice, [row["name"] for row in portal.get_fees()["invoices"]])
			self.assertEqual(portal.create_payment(invoices=[invoice]), order)
			dashboard = portal.get_dashboard()
			self.assertTrue(dashboard["restricted"])
			self.assertEqual(dashboard["student_status"], "Suspended")
			self.assertIsNone(dashboard["today_menu"])
			self.assertEqual(dashboard["pending"], {})
			self.assertIsNotNone(dashboard["fees"])

			for fn in (portal.list_leaves, portal.list_grievances, portal.get_menu, portal.list_certificates):
				self.assertRefused(fn, exc=portal.PortalStudentInactive)
			self.assertRefused(
				portal.raise_grievance,
				category="Water",
				subject="No water",
				description="No water since morning.",
				exc=portal.PortalStudentInactive,
			)
			self.assertRefused(portal.apply_leave, **leave_data(), exc=portal.PortalStudentInactive)
		create.assert_called_once()

	def test_consent_comes_before_profile_photo_and_notifications(self):
		# DPDP: nothing personal is shown or changed before the privacy notice is accepted;
		# the first-run order is consent, then confirming the details.
		student = make_student(make_beds(make_hostel(), 1)[0], consent=False)
		frappe.db.set_value("Hostel Student", student.name, "photo", "/private/files/photo-missing.png")
		make_notification(student.user, "Fee due")
		with self.set_user(student.user):
			session = portal.get_session()
			self.assertTrue(session["consent_required"])
			self.assertFalse(session["profile_confirmed"])
			for fn in (
				portal.get_profile,
				portal.download_photo,
				portal.list_notifications,
				portal.mark_notifications_read,
			):
				self.assertRefused(fn, exc=portal.PortalConsentRequired)
			self.assertRefused(
				portal.confirm_profile, updates={"city": "Overwritten"}, exc=portal.PortalConsentRequired
			)
			self.assertFalse(frappe.db.get_value("Hostel Student", student.name, "city"))
			self.assertEqual(frappe.db.get_value("Hostel Student", student.name, "profile_confirmed"), 0)
			# The notice itself, the language and the session stay open.
			self.assertTrue(portal.get_privacy_notice()["consent_required"])

			portal.accept_privacy_notice(version=privacy_version())
			self.assertEqual(portal.get_profile()["name"], student.name)
			# Past the consent check now; this photo simply has no file behind it.
			self.assertRefused(portal.download_photo, exc=frappe.DoesNotExistError)
			self.assertEqual(portal.confirm_profile(updates={"city": "Karaikudi"}), {"ok": True})
			self.assertTrue(portal.get_session()["profile_confirmed"])
			self.assertTrue(portal.list_notifications())

		parent = make_guardian([student.name], consent=False)
		with self.set_user(parent.user):
			self.assertRefused(portal.get_profile, student=student.name, exc=portal.PortalConsentRequired)
			self.assertRefused(portal.download_photo, student=student.name, exc=portal.PortalConsentRequired)
			portal.accept_privacy_notice(version=privacy_version())
			self.assertEqual(portal.get_profile(student=student.name)["name"], student.name)

	def test_privacy_consent_is_required_until_accepted(self):
		hostel = make_hostel()
		student = make_student(make_beds(hostel, 1)[0], consent=False)
		with self.set_user(student.user):
			self.assertTrue(portal.get_session()["consent_required"])
			self.assertRefused(portal.get_fees, exc=portal.PortalConsentRequired)
			self.assertRefused(portal.accept_privacy_notice, version="0.0-old", exc=frappe.ValidationError)
			self.assertEqual(portal.accept_privacy_notice(version=privacy_version()), {"ok": True})
			self.assertFalse(portal.get_session()["consent_required"])
			self.assertEqual(portal.get_fees()["student"], student.name)

		parent = make_guardian([student.name], consent=False)
		with self.set_user(parent.user):
			self.assertRefused(portal.get_fees, exc=portal.PortalConsentRequired)
			self.assertRefused(portal.pending_leave_approvals, exc=portal.PortalConsentRequired)
			portal.accept_privacy_notice(version=privacy_version())
			self.assertEqual(portal.get_fees()["student"], student.name)

	# ------------------------------------------------------------------ attachments

	def test_upload_creates_private_unattached_file_owned_by_uploader(self):
		with self.set_user(self.a.user), multipart_request("leak photo.png", png_bytes()):
			result = portal.upload_attachment(purpose="grievance")

		file = frappe.db.get_value(
			"File",
			{"file_url": result["file_url"], "owner": self.a.user},
			["is_private", "attached_to_doctype", "attached_to_name"],
			as_dict=True,
		)
		self.assertTrue(file)
		self.assertEqual(file.is_private, 1)
		self.assertFalse(file.attached_to_doctype or file.attached_to_name)
		self.assertTrue(result["file_url"].startswith("/private/files/"))

	def test_portal_upload_is_accepted_whatever_url_it_came_in_on(self):
		# The File policy refuses a portal user's file from any endpoint but the portal's own;
		# upload_attachment marks its File, so an alias or proxy path cannot break uploads.
		path = "/api/method/hostel360.api.portal.grievance_upload_alias"
		frappe.flags.hostel360_enforce_upload_policy = True
		try:
			with self.set_user(self.a.user), multipart_request("leak.png", png_bytes(), path=path):
				result = portal.upload_attachment(purpose="grievance")
			self.assertTrue(
				frappe.db.exists(
					"File", {"file_url": result["file_url"], "owner": self.a.user, "is_private": 1}
				)
			)

			# Without the mark the same request is one the File policy refuses.
			before = frappe.db.count("File", {"owner": self.a.user, "is_folder": 0})
			with (
				self.set_user(self.a.user),
				patch.object(portal, "mark_portal_upload"),
				multipart_request("leak.png", png_bytes(), path=path),
			):
				self.assertRefused(
					portal.upload_attachment,
					purpose="grievance",
					exc=(frappe.ValidationError, frappe.PermissionError),
				)
			self.assertEqual(frappe.db.count("File", {"owner": self.a.user, "is_folder": 0}), before)
		finally:
			frappe.flags.hostel360_enforce_upload_policy = False

	def test_grievance_can_only_use_the_callers_own_uploads(self):
		with self.set_user(self.b.user), multipart_request("b.png", png_bytes()):
			b_file = portal.upload_attachment(purpose="grievance")["file_url"]
		with self.set_user(self.a.user):
			with multipart_request("a.png", png_bytes()):
				a_file = portal.upload_attachment(purpose="grievance")["file_url"]

			self.assertRefused(
				portal.raise_grievance,
				category="Cleanliness",
				subject="Corridor not cleaned",
				description="Not cleaned for three days.",
				attachments=[b_file],
				exc=frappe.ValidationError,
			)
			self.assertRefused(
				portal.raise_grievance,
				category="Cleanliness",
				subject="Corridor not cleaned",
				description="Not cleaned for three days.",
				attachments=["/files/public-image.png"],
				exc=frappe.ValidationError,
			)
			name = portal.raise_grievance(
				category="Cleanliness",
				subject="Corridor not cleaned",
				description="Not cleaned for three days.",
				attachments=json.dumps([a_file]),
			)["name"]

		self.assertTrue(
			frappe.db.exists(
				"File",
				{
					"file_url": a_file,
					"owner": self.a.user,
					"attached_to_doctype": "Hostel Grievance",
					"attached_to_name": name,
				},
			)
		)
		# B's upload is untouched and still unattached.
		self.assertTrue(
			frappe.db.exists(
				"File", {"file_url": b_file, "owner": self.b.user, "attached_to_name": ("is", "not set")}
			)
		)

	def test_upload_rejects_unsafe_files(self):
		with self.set_user(self.a.user):
			cases = (
				("setup.exe", b"MZ\x90\x00" + b"\x00" * 100),
				("fake.png", b"<script>alert(1)</script>" * 10),
				("big.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * portal.UPLOAD_MAX_BYTES),
				("empty.pdf", b""),
			)
			for filename, content in cases:
				with multipart_request(filename, content):
					self.assertRefused(
						portal.upload_attachment, purpose="grievance", exc=frappe.ValidationError
					)
			with multipart_request("ok.png", png_bytes()):
				self.assertRefused(portal.upload_attachment, purpose="profile", exc=frappe.ValidationError)
			# No request file at all.
			self.assertRefused(portal.upload_attachment, purpose="grievance", exc=frappe.ValidationError)

		self.assertFalse(frappe.db.exists("File", {"owner": self.a.user, "file_name": ("like", "setup%")}))

	def test_unused_uploads_are_capped_and_cleaned_up(self):
		owner = make_student(make_beds(make_hostel(), 1)[0])
		with self.set_user(owner.user):
			with multipart_request("old.png", png_bytes()):
				old = portal.upload_attachment()["file_url"]
			with multipart_request("new.png", png_bytes()):
				new = portal.upload_attachment()["file_url"]
			with (
				patch.object(portal, "UNATTACHED_UPLOADS_PER_DAY", 2),
				multipart_request("more.png", png_bytes()),
			):
				self.assertRefused(portal.upload_attachment, exc=frappe.RateLimitExceededError)

		old_name = frappe.db.get_value("File", {"file_url": old, "owner": owner.user})
		new_name = frappe.db.get_value("File", {"file_url": new, "owner": owner.user})
		frappe.db.set_value(
			"File", old_name, "creation", add_to_date(now_datetime(), hours=-30), update_modified=False
		)

		self.assertGreaterEqual(portal.delete_stale_uploads(), 1)
		self.assertFalse(frappe.db.exists("File", old_name))
		self.assertTrue(frappe.db.exists("File", new_name))

	# ------------------------------------------------------------------ notifications

	def test_notifications_are_private_to_each_user(self):
		mine = make_notification(self.a.user, "Fee due")
		desk_link = make_notification(self.a.user, "Desk notice", link="/desk/hostel-fee-invoice")
		theirs = make_notification(self.b.user, "Someone else's fee")

		with self.set_user(self.a.user):
			rows = {row["name"]: row for row in portal.list_notifications(limit=50)}
			self.assertIn(mine, rows)
			self.assertNotIn(theirs, rows)
			self.assertEqual(rows[mine]["link"], "/hostel/fees")
			self.assertIsNone(rows[desk_link]["link"])
			self.assertNotIn("<p>", rows[mine]["message"])
			self.assertGreaterEqual(portal.get_session()["unread_notifications"], 2)

			portal.mark_notifications_read(names=[theirs, mine])
			self.assertEqual(frappe.db.get_value("Notification Log", mine, "read"), 1)
			self.assertEqual(frappe.db.get_value("Notification Log", desk_link, "read"), 0)
			portal.mark_notifications_read()
			self.assertEqual(frappe.db.get_value("Notification Log", desk_link, "read"), 1)

		self.assertEqual(frappe.db.get_value("Notification Log", theirs, "read"), 0)

	# ------------------------------------------------------------------ inputs

	def test_inputs_are_validated_before_any_write(self):
		with self.set_user(self.a.user):
			self.assertRefused(
				portal.raise_grievance,
				category="Aliens",
				subject="x",
				description="y",
				exc=frappe.ValidationError,
			)
			self.assertRefused(
				portal.raise_grievance,
				category="Water",
				subject="x" * 200,
				description="y",
				exc=frappe.ValidationError,
			)
			self.assertRefused(
				portal.raise_grievance,
				category="Water",
				subject="Leak",
				description="  ",
				exc=frappe.ValidationError,
			)
			self.assertRefused(
				portal.submit_feedback, feedback_type="Mess", overall=7, exc=frappe.ValidationError
			)
			self.assertRefused(
				portal.submit_feedback, feedback_type="Party", overall=4, exc=frappe.ValidationError
			)
			self.assertRefused(portal.request_bonafide, purpose="Fun", exc=frappe.ValidationError)
			self.assertRefused(
				portal.apply_mess_reduction,
				from_date="2026-02-30",
				to_date="2026-03-04",
				reason="Medical",
				exc=frappe.ValidationError,
			)
			self.assertRefused(
				portal.apply_mess_reduction,
				from_date=add_days(nowdate(), 3),
				to_date=add_days(nowdate(), 8),
				reason="Holiday",
				exc=frappe.ValidationError,
			)
			bad_leave = {**leave_data(), "from_datetime": "2026-13-45 25:61:00"}
			self.assertRefused(portal.apply_leave, **bad_leave, exc=frappe.ValidationError)
			self.assertRefused(portal.get_menu, week_start="2026-99-99", exc=frappe.ValidationError)
			self.assertRefused(portal.create_payment, invoices=[], exc=frappe.ValidationError)

		with self.set_user(self.parent_a.user):
			self.assertRefused(
				portal.decide_leave, name="x" * 300, action="approve", exc=frappe.ValidationError
			)

	def test_mess_reduction_is_filed_for_the_resolved_student(self):
		with (
			patch("hostel360.hostel_mess.services.apply_mess_reduction", return_value="MRA-TEST") as apply,
			self.set_user(self.parent_a.user),
		):
			result = portal.apply_mess_reduction(
				from_date=add_days(nowdate(), 3),
				to_date=add_days(nowdate(), 8),
				reason="Medical",
				description="Surgery",
			)
		self.assertEqual(result, {"name": "MRA-TEST"})
		self.assertEqual(apply.call_args.args[0], self.a.name)
		self.assertEqual(apply.call_args.kwargs["user"], self.parent_a.user)

	def test_payment_order_is_created_for_own_or_ward_invoices(self):
		order = {"transaction": "PTX-TEST", "gateway": "razorpay", "checkout": {}}
		with patch("hostel360.hostel_fees.services.create_payment_order", return_value=order) as create:
			with self.set_user(self.parent_a.user):
				self.assertEqual(portal.create_payment(invoices=json.dumps([self.invoice_a])), order)
			self.assertEqual(create.call_args.args[:3], (self.a.name, [self.invoice_a], self.parent_a.user))
			with self.set_user(self.a.user):
				self.assertRefused(portal.create_payment, invoices=[self.invoice_b], student=self.b.name)
		self.assertEqual(create.call_count, 1)

	def test_profile_hides_internal_fields_and_confirm_ignores_locked_ones(self):
		frappe.db.set_value("Hostel Student", self.a.name, "medical_notes", "Asthma")
		with self.set_user(self.a.user):
			profile = portal.get_profile()
			self.assertNotIn("medical_notes", profile)
			self.assertNotIn("mobile_number", profile)
			self.assertTrue(profile["can_edit"])
			portal.confirm_profile(updates=json.dumps({"city": "Karaikudi", "first_name": "Hacker"}))
		values = frappe.db.get_value(
			"Hostel Student", self.a.name, ["city", "first_name", "profile_confirmed"], as_dict=True
		)
		self.assertEqual(values.city, "Karaikudi")
		self.assertEqual(values.first_name, "Arun")
		self.assertEqual(values.profile_confirmed, 1)

	def test_dashboard_combines_sections(self):
		with self.set_user(self.a.user):
			dashboard = portal.get_dashboard()
		self.assertEqual(dashboard["student"], self.a.name)
		self.assertIn("outstanding", dashboard["fees"])
		self.assertEqual(set(dashboard["pending"]), {"leaves", "reductions", "grievances", "certificates"})
		self.assertIsInstance(dashboard["notifications"], list)
		self.assertLessEqual(len(dashboard["notifications"]), portal.DASHBOARD_NOTIFICATIONS)

		with self.set_user(self.parent_a.user):
			self.assertIn("approvals", portal.get_dashboard()["pending"])

	def test_dashboard_says_which_sections_failed(self):
		# A failing fee lookup must not read as "no dues" on the home screen.
		with (
			patch(
				"hostel360.hostel_fees.services.get_fee_summary", side_effect=RuntimeError("Unknown column")
			),
			patch.object(frappe, "log_error"),
			self.set_user(self.a.user),
		):
			dashboard = portal.get_dashboard()
		self.assertIsNone(dashboard["fees"])
		self.assertIn("fees", dashboard["unavailable"])
		self.assertFalse(dashboard["restricted"])

		with self.set_user(self.a.user):
			self.assertEqual(portal.get_dashboard()["unavailable"], [])

	# ------------------------------------------------------------------ session details

	def test_session_carries_office_contacts_and_portal_rules(self):
		set_hostel_settings(office_phone="04565 224528", office_email="hostel.office@example.com")
		try:
			with self.set_user(self.a.user):
				settings = portal.get_session()["settings"]
			with self.set_user("Guest"):
				boot = json.loads(hostel_page.get_context(frappe._dict()).boot_json)
		finally:
			set_hostel_settings(office_phone=None, office_email=None)

		self.assertEqual(settings["office_phone"], "04565 224528")
		self.assertEqual(settings["office_email"], "hostel.office@example.com")
		self.assertEqual(boot["office_phone"], "04565 224528")
		self.assertEqual(boot["office_email"], "hostel.office@example.com")
		# The SPA's upload checks use the same limits as the server (5 MB, no GIF).
		from hostel360.security.files import PORTAL_EXTENSIONS, PORTAL_MAX_BYTES

		self.assertEqual(settings["upload"]["max_bytes"], PORTAL_MAX_BYTES)
		self.assertEqual(set(settings["upload"]["extensions"]), set(PORTAL_EXTENSIONS))
		self.assertNotIn("gif", settings["upload"]["extensions"])
		self.assertEqual(settings["mess_day_cutoff_hour"], 12)
		self.assertTrue(settings["time_zone"])

	def test_privacy_notice_text_reaches_signed_in_users_and_guests(self):
		original = frappe.db.get_single_value("Hostel Settings", "privacy_notice")
		set_hostel_settings(privacy_notice="<p>Grievance Officer: Dr. Meena</p><script>alert(1)</script>")
		try:
			with self.set_user(self.a.user):
				notice = portal.get_privacy_notice()
			with self.set_user("Guest"):
				public = portal.get_privacy_notice()
		finally:
			set_hostel_settings(privacy_notice=original)

		self.assertIn("Grievance Officer: Dr. Meena", notice["notice"])
		self.assertNotIn("<script", notice["notice"])
		self.assertEqual(notice["version"], privacy_version())
		self.assertEqual(public["notice"], notice["notice"])
		self.assertEqual(public["version"], privacy_version())
		self.assertFalse(public["consent_required"])
		self.assertIsNone(public["accepted_version"])

	def test_set_language_switches_server_messages(self):
		student = make_student(make_beds(make_hostel(), 1)[0])
		self.addCleanup(setattr, frappe.local, "lang", "en")
		with self.set_user(student.user):
			self.assertEqual(portal.set_language(lang="ta"), {"ok": True, "language": "ta"})
			self.assertEqual(frappe.db.get_value("User", student.user, "language"), "ta")
			self.assertEqual(portal.get_session()["language"], "ta")
			self.assertRefused(portal.set_language, lang="fr", exc=frappe.ValidationError)
			self.assertRefused(portal.set_language, lang="", exc=frappe.ValidationError)
			portal.set_language(lang="en")
		self.assertEqual(frappe.db.get_value("User", student.user, "language"), "en")

	# ------------------------------------------------------------------ notifications and grievances

	def test_notifications_name_the_ward_they_are_about(self):
		from hostel360.student_services import services as student_services

		hostel = make_hostel()
		beds = make_beds(hostel, 2)
		first, second = make_student(beds[0]), make_student(beds[1])
		parent = make_guardian([first.name, second.name])
		about_second = student_services.raise_grievance(
			second.name,
			{"category": "Water", "subject": "No water", "description": "No water today."},
			"Administrator",
		)
		about_stranger = student_services.raise_grievance(
			self.b.name,
			{"category": "Water", "subject": "No water", "description": "No water today."},
			"Administrator",
		)
		mine = make_notification(parent.user, "Grievance updated", link=f"/hostel/grievances/{about_second}")
		frappe.db.set_value(
			"Notification Log", mine, {"document_type": "Hostel Grievance", "document_name": about_second}
		)
		stray = make_notification(parent.user, "Grievance updated")
		frappe.db.set_value(
			"Notification Log", stray, {"document_type": "Hostel Grievance", "document_name": about_stranger}
		)

		with self.set_user(parent.user):
			rows = {row["name"]: row for row in portal.list_notifications()}
		self.assertEqual(rows[mine]["student"], second.name)
		self.assertEqual(rows[mine]["message"], "Grievance updated details")
		self.assertIsNone(rows[stray]["student"])

	def test_grievance_attachments_are_served_through_the_portal(self):
		with self.set_user(self.a.user):
			content = png_bytes()
			with multipart_request("leak.png", content):
				upload = portal.upload_attachment(purpose="grievance")
			name = portal.raise_grievance(
				category="Plumbing",
				subject="Pipe leak",
				description="Pipe leaks in bathroom.",
				attachments=[upload["file_url"]],
			)["name"]
			secret_name = portal.raise_grievance(
				category="Ragging or Harassment", subject="Seniors", description="Being troubled every night."
			)["name"]

		desk_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "statement_witness.png",
				"content": png_bytes(),
				"is_private": 1,
				"attached_to_doctype": "Hostel Grievance",
				"attached_to_name": name,
			}
		).insert(ignore_permissions=True)

		with self.set_user(self.parent_a.user):
			detail = portal.get_grievance(name=name)
			self.assertEqual([item["file_name"] for item in detail["attachments"]], [upload["file_name"]])
			attachment = detail["attachments"][0]
			self.assertNotIn("file_url", attachment)
			self.assertIn("download_grievance_attachment", attachment["url"])

			portal.download_grievance_attachment(name=name, file=attachment["name"])
			self.assertEqual(frappe.local.response.filecontent, content)
			self.assertEqual(frappe.local.response.display_content_as, "inline")
			self.assertRefused(
				portal.download_grievance_attachment,
				name=name,
				file=desk_file.name,
				exc=frappe.DoesNotExistError,
			)
			self.assertRefused(
				portal.download_grievance_attachment, name=secret_name, file=attachment["name"]
			)
		for key in ("type", "filecontent", "filename", "content_type", "display_content_as"):
			frappe.local.response.pop(key, None)

		with self.set_user(self.b.user):
			self.assertRefused(portal.download_grievance_attachment, name=name, file=attachment["name"])

	def test_timeline_marks_only_the_viewers_own_entries(self):
		with self.set_user(self.parent_a.user):
			name = portal.raise_grievance(
				category="Water",
				subject="Hot water",
				description="No hot water for a week.",
				student=self.a.name,
			)["name"]
			portal.comment_grievance(name=name, comment="Please fix this quickly.")
		with self.set_user(self.a.user):
			portal.comment_grievance(name=name, comment="Still not fixed.")
			timeline = portal.get_grievance(name=name)["timeline"]

		parent_name = frappe.utils.get_fullname(self.parent_a.user)
		by_message = {entry["message"]: entry for entry in timeline}
		self.assertFalse(by_message["Please fix this quickly."]["is_mine"])
		self.assertEqual(by_message["Please fix this quickly."]["by"], parent_name)
		self.assertTrue(by_message["Still not fixed."]["is_mine"])
		self.assertFalse(timeline[0]["is_mine"])

		with self.set_user(self.parent_a.user):
			timeline = portal.get_grievance(name=name)["timeline"]
		self.assertTrue(
			{entry["message"]: entry for entry in timeline}["Please fix this quickly."]["is_mine"]
		)

	def test_timeline_is_in_the_readers_language_and_typed_text_as_written(self):
		from hostel360.student_services.doctype.hostel_grievance.hostel_grievance import (
			ASSIGNED_MESSAGE,
			ESCALATED_MESSAGE,
			REGISTERED_MESSAGE,
		)

		catalog = tamil_catalogue()
		self.addCleanup(frappe.translate.clear_cache)
		tamil = {message.id: message.string for message in catalog if message.id and message.string}
		# "Closed" is also a catalogue entry (the status); typed, it must not be translated.
		typed = "Closed"
		for msgid in (REGISTERED_MESSAGE, ASSIGNED_MESSAGE, ESCALATED_MESSAGE, typed):
			self.assertIn(msgid, tamil)

		with self.set_user(self.a.user):
			name = portal.raise_grievance(
				category="Electrical", subject="Tube light", description="Tube light flickers all night."
			)["name"]
		with self.set_user(self.staff):
			frappe.get_doc("Hostel Grievance", name).assign_to_user(self.staff, self.staff)
		with self.set_user(self.a.user):
			portal.comment_grievance(name=name, comment=typed)
		frappe.get_doc("Hostel Grievance", name).escalate()

		assignee = frappe.utils.get_fullname(self.staff)
		self.addCleanup(setattr, frappe.local, "lang", "en")
		frappe.local.lang = "ta"
		with self.set_user(self.a.user):
			timeline = portal.get_grievance(name=name)["timeline"]

		by_message = {entry["message"]: entry for entry in timeline}
		expected = {
			REGISTERED_MESSAGE: tamil[REGISTERED_MESSAGE],
			ASSIGNED_MESSAGE.format(assignee): tamil[ASSIGNED_MESSAGE].format(assignee),
			ESCALATED_MESSAGE.format(1): tamil[ESCALATED_MESSAGE].format(1),
			typed: typed,
		}
		# `message` is as stored; `display_message` is what the reader sees.
		self.assertEqual({stored: by_message[stored]["display_message"] for stored in expected}, expected)
		self.assertTrue(by_message[typed]["is_mine"])
		self.assertFalse(by_message[ASSIGNED_MESSAGE.format(assignee)]["is_mine"])

	# ------------------------------------------------------------------ payments

	def make_transaction(
		self, student: str, status: str, gateway: str = "PayU", payment: str | None = None
	) -> str:
		doc = frappe.new_doc("Hostel Payment Transaction")
		doc.update(
			{"student": student, "status": status, "gateway": gateway, "amount": 1500, "payment": payment}
		)
		doc.name = "PTX-PT-" + uid(8)
		doc.db_insert()
		return doc.name

	def test_payment_status_after_a_redirect_checkout(self):
		settled = {"status": "Success", "payment": "PAY-X", "receipt_number": "PAY-X", "transaction": "PTX"}
		open_payu = self.make_transaction(self.a.name, "Pending", "PayU")
		open_razorpay = self.make_transaction(self.a.name, "Initiated", "Razorpay")
		failed = self.make_transaction(self.a.name, "Failed", "PayU")
		paid = self.make_transaction(self.a.name, "Success", "PayU", payment=self.payment_a)
		theirs = self.make_transaction(self.b.name, "Pending", "PayU")

		with (
			patch("hostel360.hostel_fees.services.confirm_checkout", return_value=settled) as confirm,
			self.set_user(self.a.user),
		):
			# An open PayU payment is settled with the gateway; the query string is never trusted.
			self.assertEqual(portal.get_payment_status(transaction=open_payu), {**settled, "test_mode": 0})
			confirm.assert_called_once_with(open_payu, {})

			waiting = portal.get_payment_status(transaction=open_razorpay)
			self.assertEqual(waiting["status"], "Pending")
			self.assertTrue(waiting["message"])
			self.assertEqual(portal.get_payment_status(transaction=failed)["status"], "Failed")
			done = portal.get_payment_status(transaction=paid)
			self.assertEqual((done["status"], done["payment"]), ("Success", self.payment_a))
			self.assertRefused(portal.get_payment_status, transaction=theirs)
		self.assertEqual(confirm.call_count, 1)

		with self.set_user(self.parent_a.user):
			self.assertEqual(portal.get_payment_status(transaction=paid)["status"], "Success")

	def test_online_payment_is_offered_per_ward_as_on_the_fees_page(self):
		hostel = make_hostel()
		beds = make_beds(hostel, 2)
		tester, other = make_student(beds[0]), make_student(beds[1])
		parent = make_guardian([tester.name, other.name])

		def available(student, user=None, settings=None):
			# Test mode: only the designated test payer may pay online.
			return student == tester.name

		with (
			patch("hostel360.hostel_fees.services.online_payment_available", side_effect=available) as check,
			patch.object(portal, "_payment_test_mode", return_value=True),
		):
			with self.set_user(parent.user):
				session = portal.get_session()
				wards = {ward["student"]: ward["payment_enabled"] for ward in session["wards"]}
				self.assertEqual(wards, {tester.name: True, other.name: False})
				self.assertTrue(session["settings"]["payment_enabled"])
				self.assertTrue(all(call.args[1] == parent.user for call in check.call_args_list))

				fees = portal.get_fees(student=tester.name)
				self.assertTrue(fees["payment_enabled"])
				self.assertTrue(fees["payment_test_mode"])
				self.assertTrue(portal.get_dashboard(student=tester.name)["fees"]["payment_enabled"])
				fees = portal.get_fees(student=other.name)
				self.assertFalse(fees["payment_enabled"])
				self.assertFalse(fees["payment_test_mode"])

			with self.set_user(other.user):
				session = portal.get_session()
				self.assertFalse(session["settings"]["payment_enabled"])
				self.assertFalse(session["wards"][0]["payment_enabled"])

	def test_payment_results_say_when_they_were_test_payments(self):
		test_payment = self.make_transaction(self.a.name, "Pending", "Razorpay")
		frappe.db.set_value("Hostel Payment Transaction", test_payment, "test_mode", 1)
		failed = {
			"status": "Failed",
			"payment": None,
			"receipt_number": None,
			"transaction": test_payment,
			"message": "Card declined",
		}
		with (
			patch("hostel360.hostel_fees.services.confirm_checkout", return_value=failed),
			self.set_user(self.a.user),
		):
			result = portal.verify_payment(
				transaction=test_payment,
				razorpay_payment_id="pay_TEST123",
				razorpay_order_id="order_TEST123",
				razorpay_signature="a" * 64,
			)
			self.assertEqual(result["status"], "Failed")
			self.assertEqual(result["test_mode"], 1)
			self.assertEqual(portal.get_payment_status(transaction=test_payment)["test_mode"], 1)

	# ------------------------------------------------------------------ rate limits

	def test_writes_are_rate_limited_per_user(self):
		keys = [
			frappe.cache.make_key(f"hostel360:portal-rate:accept_privacy_notice:{u}")
			for u in (self.a.user, self.b.user)
		]
		frappe.flags.hostel360_enforce_rate_limits = True
		try:
			with patch.dict(portal.RATE_LIMITS, {"accept_privacy_notice": (2, 60)}):
				with self.set_user(self.a.user):
					portal.accept_privacy_notice(version=privacy_version())
					portal.accept_privacy_notice(version=privacy_version())
					self.assertRefused(
						portal.accept_privacy_notice,
						version=privacy_version(),
						exc=frappe.RateLimitExceededError,
					)
				with self.set_user(self.b.user):
					self.assertEqual(portal.accept_privacy_notice(version=privacy_version()), {"ok": True})
		finally:
			frappe.flags.hostel360_enforce_rate_limits = False
			frappe.cache.delete(*keys)


class TestPortalTranslations(IntegrationTestCase):
	"""Server messages follow the portal's Tamil switch (hostel360/locale/ta.po)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.catalog = tamil_catalogue()
		cls.translated = {
			message.id: message.string for message in cls.catalog if message.id and message.string
		}

	def tearDown(self):
		frappe.local.lang = "en"
		frappe.translate.clear_cache()
		super().tearDown()

	def portal_messages(self, path: str) -> set[str]:
		from babel.messages.extract import extract_from_file

		return {
			message
			for _lineno, message, _comments, _context in extract_from_file(
				"python", path, keywords={"_": None}
			)
			if isinstance(message, str) and message
		}

	def test_every_portal_message_has_a_tamil_translation(self):
		for path in (portal.__file__, hostel_page.__file__):
			missing = sorted(self.portal_messages(path) - set(self.translated))
			self.assertEqual(missing, [], f"untranslated in {os.path.basename(path)}")

	def test_translations_keep_their_placeholders(self):
		placeholder = re.compile(r"\{\d*\}")
		for msgid, text in self.translated.items():
			self.assertEqual(sorted(placeholder.findall(msgid)), sorted(placeholder.findall(text)), msgid)

	def test_server_messages_are_tamil_for_a_tamil_user(self):
		message = "Please choose to approve or reject the leave."
		frappe.local.lang = "ta"
		self.assertEqual(frappe._(message), self.translated[message])
		self.assertNotEqual(frappe._(message), message)
		frappe.local.lang = "en"
		self.assertEqual(frappe._(message), message)


class TestPortalPage(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.student = make_student(first_name="Dinesh")
		cls.staff = make_user([ADMIN_ROLE], website_user=False)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.flags.redirect_location = None
		super().tearDown()

	def test_guest_gets_boot_data_without_token(self):
		with self.set_user("Guest"):
			context = hostel_page.get_context(frappe._dict())
		self.assertEqual(context.no_cache, 1)
		self.assertEqual(context.csrf_token, "")
		boot = json.loads(context.boot_json)
		self.assertEqual(set(boot), BOOT_KEYS)
		self.assertFalse(boot["is_logged_in"])
		self.assertIsNone(boot["kind"])
		self.assertIsNone(boot["demo"])
		self.assertEqual(boot["site_name"], frappe.local.site)

	def test_student_gets_session_token(self):
		with self.set_user(self.student.user):
			context = hostel_page.get_context(frappe._dict())
			self.assertTrue(context.csrf_token)
			self.assertEqual(context.csrf_token, frappe.local.session.data.csrf_token)
		boot = json.loads(context.boot_json)
		self.assertTrue(boot["is_logged_in"])
		self.assertEqual(boot["kind"], "student")

	def test_boot_json_is_safe_inside_a_script_element(self):
		hostile = "</script><img src=x onerror=alert(1)> & \u2028"
		with (
			patch("hostel360.api.portal.get_branding", return_value={"institution_name": hostile}),
			self.set_user("Guest"),
		):
			context = hostel_page.get_context(frappe._dict())
		for character in ("<", ">", "&", "\u2028"):
			self.assertNotIn(character, str(context.boot_json))
		self.assertEqual(json.loads(context.boot_json)["institution_name"], hostile)

	def test_boot_says_whether_email_codes_are_offered(self):
		# Guests see it before typing a number; it is the site-wide switch and nothing else.
		for offered in (True, False):
			with (
				patch("hostel360.auth.otp.email_codes_enabled", return_value=offered) as enabled,
				self.set_user("Guest"),
			):
				boot = json.loads(hostel_page.get_context(frappe._dict()).boot_json)
			self.assertIs(boot["otp_email"], offered)
			enabled.assert_called_once_with()

	def test_unreadable_settings_hide_email_codes(self):
		with (
			patch("hostel360.auth.otp.email_codes_enabled", side_effect=RuntimeError("no settings")),
			self.set_user("Guest"),
		):
			boot = json.loads(hostel_page.get_context(frappe._dict()).boot_json)
		self.assertFalse(boot["otp_email"])

	def test_desk_user_is_sent_to_the_desk(self):
		with self.set_user(self.staff):
			with self.assertRaises(frappe.Redirect) as raised:
				hostel_page.get_context(frappe._dict())
		self.assertEqual(raised.exception.http_status_code, 302)
		self.assertEqual(frappe.local.flags.redirect_location, "/desk")
