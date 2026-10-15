# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AcademicDepartment(Document):
	def before_naming(self):
		self.department_name = " ".join((self.department_name or "").split())

	def validate(self):
		self.department_name = " ".join((self.department_name or "").split())
		if self.department_code:
			self.department_code = self.department_code.strip().upper()
