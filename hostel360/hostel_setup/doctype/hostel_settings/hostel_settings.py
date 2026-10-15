# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class HostelSettings(Document):
	def validate(self):
		self.validate_hours_and_days()
		self.clean_urls()
		if not (self.privacy_notice_version or "").strip():
			frappe.throw(_("Privacy Notice Version is required."))
		self.privacy_notice_version = self.privacy_notice_version.strip()

	def on_update(self):
		if self.has_value_changed("current_academic_year"):
			sync_current_academic_year(self.current_academic_year)

	def validate_hours_and_days(self):
		for fieldname in (
			"leave_return_grace_hours",
			"grievance_escalation_hours",
			"grievance_urgent_escalation_hours",
			"bonafide_validity_days",
		):
			if cint(self.get(fieldname)) < 0:
				frappe.throw(_("{0} cannot be negative.").format(_(self.meta.get_label(fieldname))))

		if cint(self.grievance_escalation_hours) <= 0:
			frappe.throw(_("Grievance Escalation Hours must be at least 1."))
		if cint(self.grievance_urgent_escalation_hours) <= 0:
			frappe.throw(_("Grievance Urgent Escalation Hours must be at least 1."))
		if cint(self.grievance_urgent_escalation_hours) > cint(self.grievance_escalation_hours):
			frappe.throw(
				_("Urgent grievances must escalate sooner than normal ones. Reduce Grievance Urgent Escalation Hours.")
			)

	def clean_urls(self):
		for fieldname in ("portal_url", "hostel_website_url"):
			value = (self.get(fieldname) or "").strip()
			if value and not value.startswith(("https://", "http://")):
				frappe.throw(
					_("{0} must start with https://").format(_(self.meta.get_label(fieldname)))
				)
			self.set(fieldname, value.rstrip("/"))


def sync_current_academic_year(academic_year: str | None) -> None:
	"""Keep exactly one Hostel Academic Year flagged as current, matching the settings."""
	if academic_year:
		frappe.db.sql(
			"""update `tabHostel Academic Year` set is_current = 0
			where is_current = 1 and name != %(name)s""",
			{"name": academic_year},
		)
		frappe.db.set_value("Hostel Academic Year", academic_year, "is_current", 1, update_modified=False)
	else:
		frappe.db.sql("update `tabHostel Academic Year` set is_current = 0 where is_current = 1")


def get_current_academic_year() -> str | None:
	return frappe.db.get_single_value("Hostel Settings", "current_academic_year")
