# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, getdate

ACADEMIC_YEAR_RE = re.compile(r"^(\d{4})-(\d{4})$")


class HostelAcademicYear(Document):
	def validate(self):
		self.academic_year = (self.academic_year or "").strip()
		match = ACADEMIC_YEAR_RE.match(self.academic_year)
		if not match or int(match.group(2)) != int(match.group(1)) + 1:
			frappe.throw(_("Academic Year must look like 2026-2027 (two consecutive years)."))

		if getdate(self.start_date) >= getdate(self.end_date):
			frappe.throw(_("Start Date must be before End Date."))
		if date_diff(self.end_date, self.start_date) > 400:
			frappe.throw(_("An academic year cannot be longer than 400 days."))

		self.validate_overlap()

	def validate_overlap(self):
		overlapping = frappe.db.sql(
			"""
			select name from `tabHostel Academic Year`
			where name != %(name)s and start_date <= %(end_date)s and end_date >= %(start_date)s
			limit 1
			""",
			{"name": self.name or "", "start_date": self.start_date, "end_date": self.end_date},
		)
		if overlapping:
			frappe.throw(
				_("The dates overlap with academic year {0}.").format(frappe.bold(overlapping[0][0]))
			)

	def on_update(self):
		from hostel360.hostel_setup.doctype.hostel_settings.hostel_settings import sync_current_academic_year

		settings_year = frappe.db.get_single_value("Hostel Settings", "current_academic_year")
		if self.is_current:
			sync_current_academic_year(self.name)
			if settings_year != self.name:
				frappe.db.set_single_value("Hostel Settings", "current_academic_year", self.name)
		elif settings_year == self.name:
			frappe.db.set_single_value("Hostel Settings", "current_academic_year", None)

	def on_trash(self):
		if frappe.db.get_single_value("Hostel Settings", "current_academic_year") == self.name:
			frappe.db.set_single_value("Hostel Settings", "current_academic_year", None)


def academic_year_for_date(date=None) -> tuple[str, str, str]:
	"""Academic year label and bounds (June to May) that contain ``date``."""
	date = getdate(date)
	start_year = date.year if date.month >= 6 else date.year - 1
	return (
		f"{start_year}-{start_year + 1}",
		f"{start_year}-06-01",
		f"{start_year + 1}-05-31",
	)
