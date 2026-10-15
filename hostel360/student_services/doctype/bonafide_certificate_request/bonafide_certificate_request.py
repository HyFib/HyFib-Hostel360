# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import secrets

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import add_days, cint, getdate, now_datetime, nowdate

from hostel360.student_services import notifications
from hostel360.student_services.utils import (
	assert_student_eligible,
	assert_unchanged,
	assert_warden_approver,
	get_setting,
	get_student,
	get_verification_url,
	get_warden_approvers,
	in_migration,
	is_staff_user,
	load_for_action,
	recheck_scope,
	student_hostel,
)

CERTIFIED_STATUSES = ("Approved", "Issued")
# What the certificate says about the student, copied from the student when the request is
# made. The student's name is fetched from the student record by Frappe on every save.
CERTIFICATE_FIELDS = ("student", "program", "department", "year_of_study", "academic_year", "hostel", "room")
# Set only by approve_by, reject_by and mark_issued_by. The public verification page trusts
# these, so an ordinary save may never change them.
DECISION_FIELDS = (
	"status",
	"approved_by",
	"approved_on",
	"rejection_reason",
	"certificate_number",
	"issue_date",
	"valid_till",
	"verification_code",
	"issued_on",
	"issued_by",
)
YEAR_WORDS = {"1": "first", "2": "second", "3": "third", "4": "fourth", "5": "fifth"}
PRONOUNS = {
	"Male": {"title": "Mr.", "relation": "son", "subject": "He"},
	"Female": {"title": "Ms.", "relation": "daughter", "subject": "She"},
}
NEUTRAL_PRONOUNS = {"title": "", "relation": "ward", "subject": "The student"}
# Completes "issued at the request of the student for ..." on the certificate.
PURPOSE_PHRASES = {
	"Bank Loan": "applying for a bank loan",
	"Scholarship": "applying for a scholarship",
	"Passport": "applying for a passport",
	"Visa": "applying for a visa",
	"Internship": "an internship application",
	"Address Proof": "use as proof of address",
}


class BonafideCertificateRequest(Document):
	def onload(self):
		if self.status in CERTIFIED_STATUSES and self.verification_code:
			self.set_onload("verification_url", get_verification_url(self.verification_code))

	def validate(self):
		self.purpose_details = (self.purpose_details or "").strip() or None
		if self.is_new():
			student = self.set_student_details()
			if not in_migration():
				# A request starts undecided, whatever the request said: a code or number planted
				# here would be vouched for by the public verification page once approved.
				for fieldname in DECISION_FIELDS:
					self.set(fieldname, None)
				self.status = "Pending"
		else:
			self.validate_protected_fields()
		# Before any check whose message would describe another hostel's records.
		recheck_scope(self)
		if self.is_new() and not in_migration():
			assert_student_eligible(student, _("A bonafide certificate"))
			self.validate_duplicate()
		if self.purpose == "Other" and not self.purpose_details:
			frappe.throw(_("Please describe the purpose of the certificate."))
		self.validate_locked()

	def after_insert(self):
		if self.status == "Pending":
			self.notify_approvers()

	def validate_protected_fields(self):
		if in_migration():
			return
		assert_unchanged(
			self, CERTIFICATE_FIELDS, _("{0} cannot be changed. Please raise a new request if they are wrong.")
		)
		if not self.flags.recording_decision:
			assert_unchanged(
				self, DECISION_FIELDS, _("{0} can only be changed with the Approve, Reject and Mark Issued actions.")
			)

	def set_student_details(self) -> frappe._dict:
		student = get_student(self.student)
		self.student_name = student.full_name
		if in_migration() and self.hostel:
			return student
		self.program = student.get("program")
		self.department = student.get("department")
		self.year_of_study = student.get("year_of_study")
		self.academic_year = get_setting("current_academic_year") or student.get("academic_year")
		self.hostel = student_hostel(student)
		self.room = student.get("current_room")
		return student

	def validate_duplicate(self):
		existing = frappe.db.get_value(
			"Bonafide Certificate Request",
			{"student": self.student, "purpose": self.purpose, "status": "Pending", "name": ["!=", self.name or ""]},
		)
		if existing:
			frappe.throw(
				_("A request for a {0} certificate is already pending ({1}).").format(_(self.purpose), existing),
				frappe.DuplicateEntryError,
			)

	def validate_locked(self):
		"""What is printed on an approved certificate may not be edited afterwards."""
		if self.is_new() or in_migration():
			return
		before = self.get_doc_before_save()
		if not before or before.status not in CERTIFIED_STATUSES:
			return
		for fieldname in ("purpose", "purpose_details"):
			if self.has_value_changed(fieldname):
				frappe.throw(_("An approved certificate cannot be changed. Please raise a new request."))

	# ------------------------------------------------------------------ decisions

	def save_decision(self):
		self.flags.ignore_permissions = True
		self.flags.recording_decision = True
		try:
			self.save()
		finally:
			self.flags.recording_decision = False

	def approve_by(self, user: str):
		assert_warden_approver(user, self.hostel, self.student)
		if self.status != "Pending":
			frappe.throw(_("This request has already been decided (it is {0}).").format(_(self.status)))
		today = getdate(nowdate())
		self.status = "Approved"
		self.approved_by = user
		self.approved_on = now_datetime()
		self.issue_date = today
		self.valid_till = add_days(today, max(cint(get_setting("bonafide_validity_days")), 1))
		self.certificate_number = next_certificate_number(today.year)
		self.verification_code = new_verification_code()
		self.rejection_reason = None
		self.save_decision()
		self.notify_student()

	def reject_by(self, reason: str, user: str):
		assert_warden_approver(user, self.hostel, self.student)
		if self.status != "Pending":
			frappe.throw(_("This request has already been decided (it is {0}).").format(_(self.status)))
		reason = (reason or "").strip()
		if not reason:
			frappe.throw(_("Please give a reason for rejecting the request."))
		self.status = "Rejected"
		self.approved_by = user
		self.approved_on = now_datetime()
		self.rejection_reason = reason
		self.save_decision()
		self.notify_student()

	def mark_issued_by(self, user: str):
		# Not self.has_permission(): an earlier action on this object leaves ignore_permissions set.
		if not is_staff_user(user) or not frappe.has_permission(self.doctype, "write", doc=self, user=user):
			frappe.throw(_("You are not permitted to update this request."), frappe.PermissionError)
		if self.status != "Approved":
			frappe.throw(_("Only an approved certificate can be handed over."))
		self.status = "Issued"
		self.issued_on = now_datetime()
		self.issued_by = user
		self.save_decision()

	def notify_student(self):
		notifications.send(
			"bonafide_decided",
			notifications.for_student(self.student),
			notifications.bonafide_context(self),
			self,
		)

	def notify_approvers(self):
		"""The wardens who may sign the certificate, and the student's deputy wardens."""
		notifications.send(
			"bonafide_requested",
			notifications.for_users(get_warden_approvers(self.hostel, self.student)),
			notifications.bonafide_context(self),
			self,
		)

	# ------------------------------------------------------------------ printing

	def before_print(self, print_settings=None):
		self.is_certified = self.status in CERTIFIED_STATUSES and bool(self.verification_code)
		self.verification_url = get_verification_url(self.verification_code) if self.is_certified else ""
		self.qr_code_data_uri = ""
		if self.is_certified:
			from hostel360.student_services.qr import qr_png_data_uri

			self.qr_code_data_uri = qr_png_data_uri(self.verification_url)
		self.certificate = self.get_certificate_context()

	def get_certificate_context(self) -> frappe._dict:
		from hostel360.utils.jinja import get_institution_details

		gender = frappe.db.get_value("Hostel Student", self.student, "gender")
		pronouns = PRONOUNS.get(gender, NEUTRAL_PRONOUNS)
		institution = get_institution_details()
		contact = [
			institution.get("phone") and f"Phone: {institution['phone']}",
			institution.get("email") and f"Email: {institution['email']}",
			institution.get("website"),
		]
		return frappe._dict(
			title=pronouns["title"],
			relation=pronouns["relation"],
			subject=pronouns["subject"],
			guardian_name=self.get_primary_guardian_name(),
			year_words=YEAR_WORDS.get(str(self.year_of_study or ""), ""),
			hostel_name=(self.hostel and frappe.db.get_value("Hostel", self.hostel, "hostel_name")) or self.hostel,
			room_number=(self.room and frappe.db.get_value("Hostel Room", self.room, "room_number")) or "",
			purpose_phrase=self.get_purpose_phrase(),
			contact_line="  |  ".join(part for part in contact if part),
			signatory_name=get_setting("bonafide_signatory_name") or "",
			signatory_designation=get_setting("bonafide_signatory_designation") or "Warden",
			verify_base_url=get_verification_url("").split("?")[0],
		)

	def get_purpose_phrase(self) -> str:
		phrase = PURPOSE_PHRASES.get(self.purpose)
		if not phrase:
			return self.purpose_details or (self.purpose or "").lower()
		return f"{phrase} ({self.purpose_details})" if self.purpose_details else phrase

	def get_primary_guardian_name(self) -> str:
		rows = frappe.get_all(
			"Hostel Student Guardian",
			filters={"parent": self.student, "parenttype": "Hostel Student"},
			fields=["guardian_name", "relation", "is_primary"],
			order_by="is_primary desc, idx asc",
			ignore_permissions=True,
		)
		# Certificates traditionally name the father or mother ("son of ..."); other relations
		# are left out rather than printed wrongly.
		for row in rows:
			if row.relation in ("Father", "Mother") and row.guardian_name:
				return row.guardian_name
		return ""

	# ------------------------------------------------------------------ desk actions
	# The form sends its own copy of the request with these calls; each action works on the
	# stored record and then refreshes the form's copy from it.

	@frappe.whitelist(methods=["POST"])
	def approve(self):
		load_for_action(self).approve_by(frappe.session.user)
		self.reload()

	@frappe.whitelist(methods=["POST"])
	def reject(self, reason: str):
		load_for_action(self).reject_by(reason, frappe.session.user)
		self.reload()

	@frappe.whitelist(methods=["POST"])
	def mark_issued(self):
		load_for_action(self).mark_issued_by(frappe.session.user)
		self.reload()


def next_certificate_number(year: int) -> str:
	"""BC/2026/00042 - a counter of its own per calendar year, separate from the request series."""
	prefix = f"BC/{year}/"
	return prefix + getseries(prefix, 5)


def new_verification_code() -> str:
	# 12 random bytes give exactly 16 URL-safe characters (96 bits): not guessable, short enough to type.
	while True:
		code = secrets.token_urlsafe(12)
		if not frappe.db.exists("Bonafide Certificate Request", {"verification_code": code}):
			return code
