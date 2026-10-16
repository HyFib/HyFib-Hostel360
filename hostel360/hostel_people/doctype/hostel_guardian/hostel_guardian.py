# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address

from hostel360.hostel_people.protection import keep_system_fields
from hostel360.utils.validation import normalize_mobile

SYSTEM_FIELDS = ("user", "privacy_consent_version", "privacy_consent_on")


class HostelGuardian(Document):
	def validate(self):
		keep_system_fields(self, SYSTEM_FIELDS)
		self.guardian_name = " ".join((self.guardian_name or "").split())
		self.mobile_number = normalize_mobile(self.mobile_number)
		if self.email:
			self.email = self.email.strip().lower()
			validate_email_address(self.email, throw=True)
		self.validate_unique_mobile()
		self.set_validation_status()

	def validate_unique_mobile(self):
		duplicate = frappe.db.get_value(
			"Hostel Guardian",
			{"mobile_number": self.mobile_number, "name": ["!=", self.name or ""]},
			["name", "guardian_name"],
			as_dict=True,
		)
		if not duplicate:
			return
		if frappe.has_permission("Hostel Guardian", "read", doc=duplicate.name):
			message = _(
				"Mobile number {0} is already registered for guardian {1} ({2}). Link that guardian instead."
			).format(frappe.bold(self.mobile_number), duplicate.guardian_name, duplicate.name)
		else:
			# The parent of a student in another hostel: say how to link them without naming them.
			message = _(
				"Mobile number {0} is already registered for a guardian. Type it in the Guardian column of the student's Guardians table to link them."
			).format(frappe.bold(self.mobile_number))
		frappe.throw(message, frappe.UniqueValidationError)

	def set_validation_status(self):
		"""A guardian is validated once linked to a validated student (tender 4.1).

		A changed mobile number is re-checked against the wards: the guardian stays
		Validated only while a validated student still lists them, otherwise it goes back
		to Pending. Only staff who may edit every ward can change the number (see
		``hostel360.hostel_people.permissions.guardian_has_permission``), so the person
		making the change is the one confirming it.
		"""
		if self.is_new() or self.validation_status == "Rejected":
			return
		if self.has_value_changed("mobile_number"):
			self.validation_status = "Pending"
		if self.validation_status == "Pending" and self.get_validated_wards():
			self.validation_status = "Validated"

	def get_validated_wards(self) -> list[str]:
		return frappe.get_all(
			"Hostel Student",
			filters=[
				["Hostel Student Guardian", "guardian", "=", self.name],
				["validation_status", "=", "Validated"],
			],
			pluck="name",
			ignore_permissions=True,
		)

	def onload(self):
		from hostel360.hostel_people.services import can_unlock_sign_in

		self.set_onload("can_unlock_sign_in", bool(self.mobile_number) and can_unlock_sign_in())

	def on_update(self):
		if self.has_value_changed("guardian_name") or self.has_value_changed("mobile_number"):
			# Child rows copy these through fetch_from only when the student is saved.
			frappe.db.set_value(
				"Hostel Student Guardian",
				{"guardian": self.name, "parenttype": "Hostel Student"},
				{"guardian_name": self.guardian_name, "mobile_number": self.mobile_number},
				update_modified=False,
			)

	@frappe.whitelist()
	def get_wards(self) -> list[dict]:
		self.check_permission("read")
		rows = frappe.get_all(
			"Hostel Student Guardian",
			filters={"guardian": self.name, "parenttype": "Hostel Student"},
			fields=["parent", "relation", "is_primary", "can_approve_leave"],
			ignore_permissions=True,
		)
		wards = []
		for row in rows:
			student = frappe.db.get_value(
				"Hostel Student",
				row.parent,
				["name", "full_name", "status", "validation_status", "current_hostel", "current_room"],
				as_dict=True,
			)
			if student and frappe.has_permission("Hostel Student", "read", student.name):
				wards.append({**student, **row})
		return wards

	@frappe.whitelist(methods=["POST"])
	def create_portal_user(self) -> str:
		if not frappe.has_permission(self.doctype, "write", self):
			frappe.throw(_("You are not permitted to create portal users."), frappe.PermissionError)
		from hostel360.hostel_people.services import ensure_portal_user

		return ensure_portal_user(self.doctype, self.name)
