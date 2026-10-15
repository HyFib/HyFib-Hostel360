# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, now_datetime, nowdate, validate_email_address

from hostel360.hostel_people.protection import keep_system_fields
from hostel360.setup.roles import ADMIN_ROLE, DEPUTY_WARDEN_ROLE, OFFICE_ROLE, WARDEN_ROLE
from hostel360.utils.context import has_any_role
from hostel360.utils.validation import normalize_mobile, validate_pincode

# Staff who may validate or reject a student's registration (tender 3.1).
VALIDATOR_ROLES = frozenset({ADMIN_ROLE, WARDEN_ROLE, DEPUTY_WARDEN_ROLE, OFFICE_ROLE, "System Manager"})

# A student in these statuses no longer lives in the hostel.
LEFT_STATUSES = ("Vacated", "Alumni")

# Maintained by allotments, the validation actions and the portal services only.
SYSTEM_FIELDS = (
	"current_hostel",
	"last_hostel",
	"current_room",
	"current_bed",
	"current_allotment",
	"user",
	"validation_status",
	"validated_by",
	"validated_on",
	"rejection_reason",
	"profile_confirmed",
	"privacy_consent_version",
	"privacy_consent_on",
)
SYSTEM_FIELD_DEFAULTS = {"validation_status": "Pending", "profile_confirmed": 0}

# The only protected fields the validate and reject actions write.
VALIDATION_FIELDS = ("validation_status", "validated_by", "validated_on", "rejection_reason")


class HostelStudent(Document):
	def before_naming(self):
		self.register_number = clean_register_number(self.register_number)

	def validate(self):
		keep_system_fields(self, SYSTEM_FIELDS, SYSTEM_FIELD_DEFAULTS)
		if self.is_new():
			self.register_number = clean_register_number(self.register_number)
		self.first_name = " ".join((self.first_name or "").split())
		self.last_name = " ".join((self.last_name or "").split())
		self.full_name = " ".join(part for part in (self.first_name, self.last_name) if part)

		self.mobile_number = normalize_mobile(self.mobile_number)
		self.emergency_contact_phone = normalize_mobile(self.emergency_contact_phone)
		if self.email:
			self.email = self.email.strip().lower()
			validate_email_address(self.email, throw=True)
		validate_pincode(self.pincode)
		self.validate_unique_mobile()
		self.validate_dates()

		if not self.academic_year:
			self.academic_year = frappe.db.get_single_value("Hostel Settings", "current_academic_year")

		self.validate_guardians()
		self.validate_status_change()
		self.validate_gender_change()

	def validate_unique_mobile(self):
		duplicate = frappe.db.get_value(
			"Hostel Student",
			{"mobile_number": self.mobile_number, "name": ["!=", self.name or ""]},
			["name", "full_name"],
			as_dict=True,
		)
		if duplicate:
			frappe.throw(
				_("Mobile number {0} is already registered for {1} ({2}).").format(
					frappe.bold(self.mobile_number), duplicate.full_name, duplicate.name
				),
				frappe.UniqueValidationError,
			)

	def validate_dates(self):
		if self.date_of_birth and getdate(self.date_of_birth) >= getdate(nowdate()):
			frappe.throw(_("Date of Birth must be in the past."))
		if self.admission_year and not 1950 <= cint(self.admission_year) <= getdate(nowdate()).year + 1:
			frappe.throw(_("Admission Year {0} does not look right.").format(self.admission_year))

	def validate_guardians(self):
		seen = set()
		primary_rows = []
		for row in self.guardians:
			if row.guardian in seen:
				frappe.throw(
					_("Row {0}: guardian {1} is listed more than once.").format(row.idx, frappe.bold(row.guardian))
				)
			seen.add(row.guardian)
			if row.is_primary:
				primary_rows.append(row)

		if len(primary_rows) > 1:
			frappe.throw(_("Only one guardian can be the primary contact."))
		if self.guardians and not primary_rows:
			self.guardians[0].is_primary = 1

	def validate_status_change(self):
		if self.is_new() or not self.has_value_changed("status"):
			return
		if self.status in LEFT_STATUSES and self.current_allotment:
			frappe.throw(
				_("{0} still holds bed {1}. Vacate the room allotment before marking the student {2}.").format(
					self.full_name, frappe.bold(self.current_bed), _(self.status)
				)
			)

	def validate_gender_change(self):
		if self.is_new() or not self.has_value_changed("gender") or not self.current_hostel:
			return
		hostel_gender = frappe.get_cached_value("Hostel", self.current_hostel, "gender")
		if hostel_gender in ("Male", "Female") and hostel_gender != self.gender:
			frappe.throw(
				_("{0} lives in a {1} hostel. Transfer the student before changing the gender.").format(
					self.full_name, _(hostel_gender)
				)
			)

	def onload(self):
		from hostel360.hostel_people.services import can_unlock_sign_in

		self.set_onload("can_unlock_sign_in", bool(self.mobile_number) and can_unlock_sign_in())

	def on_update(self):
		if self.validation_status == "Validated":
			validate_linked_guardians(self)

	def can_validate(self) -> bool:
		return has_any_role(VALIDATOR_ROLES) and frappe.has_permission(self.doctype, "write", self)

	@frappe.whitelist(methods=["POST"])
	def mark_validated(self) -> None:
		"""Confirm the registration after checking it against admission records."""
		# The form sends its own copy of the record with the call. Work from the stored one,
		# so neither the checks below nor the save can use values the browser supplied.
		self.reload()
		if not self.can_validate():
			frappe.throw(_("You are not permitted to validate students."), frappe.PermissionError)
		if self.validation_status == "Validated":
			return

		self.validation_status = "Validated"
		self.validated_by = frappe.session.user
		self.validated_on = now_datetime()
		self.rejection_reason = None
		self.flags.allow_system_fields = VALIDATION_FIELDS
		self.save()
		self.add_comment("Info", _("Registration validated"))

		from hostel360.hostel_people.notifications import notify_student

		notify_student(
			"student_validated",
			self.name,
			{
				"student_name": self.full_name,
				"register_number": self.register_number,
				"institution_short_name": frappe.db.get_single_value("Hostel Settings", "institution_short_name"),
			},
			reference_doctype=self.doctype,
			reference_name=self.name,
		)

	@frappe.whitelist(methods=["POST"])
	def mark_rejected(self, reason: str) -> None:
		"""Reject a registration that does not match admission records."""
		self.reload()
		if not self.can_validate():
			frappe.throw(_("You are not permitted to reject students."), frappe.PermissionError)
		reason = (reason or "").strip()
		if len(reason) < 5:
			frappe.throw(_("Please give a reason for the rejection."))
		if self.current_allotment:
			frappe.throw(_("Vacate the room allotment before rejecting this registration."))

		self.validation_status = "Rejected"
		self.validated_by = frappe.session.user
		self.validated_on = now_datetime()
		self.rejection_reason = reason
		self.flags.allow_system_fields = VALIDATION_FIELDS
		self.save()
		self.add_comment("Info", _("Registration rejected: {0}").format(reason))

	@frappe.whitelist(methods=["POST"])
	def create_portal_user(self) -> str:
		"""Desk action: create the student's portal login (OTP to the registered mobile)."""
		if not frappe.has_permission(self.doctype, "write", self):
			frappe.throw(_("You are not permitted to create portal users."), frappe.PermissionError)
		from hostel360.hostel_people.services import ensure_portal_user

		return ensure_portal_user(self.doctype, self.name)


def clean_register_number(value: str | None) -> str:
	register_number = "".join((value or "").split()).upper()
	if not register_number:
		frappe.throw(_("Register Number is required."))
	if len(register_number) > 30:
		frappe.throw(_("Register Number can be at most 30 characters long."))
	if not all(ch.isalnum() or ch in "-/" for ch in register_number):
		frappe.throw(_("Register Number may contain only letters, digits, hyphens and slashes."))
	return register_number


def validate_linked_guardians(student) -> None:
	"""Validate guardians of a validated student whose registered mobile is valid (tender 4.1)."""
	from hostel360.utils.validation import is_valid_mobile

	for row in student.guardians:
		guardian = frappe.db.get_value(
			"Hostel Guardian", row.guardian, ["validation_status", "mobile_number"], as_dict=True
		)
		if guardian and guardian.validation_status == "Pending" and is_valid_mobile(guardian.mobile_number):
			frappe.db.set_value("Hostel Guardian", row.guardian, "validation_status", "Validated")
