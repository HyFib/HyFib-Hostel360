# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class MonthlyPerformanceReport(Document):
	def validate(self):
		from hostel360.hostel_operations.reports import month_bounds

		month_bounds(self.month)
		if (
			self.is_new()
			and frappe.db.exists(self.doctype, {"month": self.month, "docstatus": 1})
			and not self.amended_from
		):
			frappe.throw(
				_("A submitted report for {0} already exists.").format(self.month), frappe.DuplicateEntryError
			)

	def before_submit(self):
		if not self.prepared_on:
			from hostel360.hostel_operations.reports import populate

			populate(self)

	@frappe.whitelist()
	def refresh_figures(self):
		from hostel360.hostel_operations.reports import _require_operations, populate

		_require_operations()
		if self.docstatus != 0:
			frappe.throw(_("Figures of a submitted report cannot change. Amend it instead."))
		populate(self)
		self.save()
