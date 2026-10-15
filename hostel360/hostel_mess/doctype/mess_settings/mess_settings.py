# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class MessSettings(Document):
	def validate(self):
		if cint(self.reduction_min_days) < 1:
			frappe.throw(_("Minimum days per application must be at least 1."))
		if cint(self.max_reduction_days_per_month) and cint(self.max_reduction_days_per_month) < cint(
			self.reduction_min_days
		):
			frappe.throw(
				_(
					"The monthly limit ({0} days) is lower than the minimum days per application ({1}); no application could be accepted."
				).format(self.max_reduction_days_per_month, self.reduction_min_days)
			)
		if cint(self.max_reduction_days_per_month) > 31:
			frappe.throw(_("Maximum reduction days per month cannot exceed 31."))
		if cint(self.bill_due_days) > 90:
			frappe.throw(_("Bill due days cannot be more than 90."))
