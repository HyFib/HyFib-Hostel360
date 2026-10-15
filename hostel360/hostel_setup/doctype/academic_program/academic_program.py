# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class AcademicProgram(Document):
	def before_naming(self):
		self.program_name = " ".join((self.program_name or "").split())

	def validate(self):
		self.program_name = " ".join((self.program_name or "").split())
		if not 1 <= cint(self.duration_years) <= 7:
			frappe.throw(_("Duration must be between 1 and 7 years."))
