# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from hostel360.hostel_setup.utils import clean_code


class HostelBlock(Document):
	def before_naming(self):
		self.block_code = clean_code(self.block_code, "Block Code", max_length=10)

	def validate(self):
		self.block_name = " ".join((self.block_name or "").split())
		if self.is_new():
			self.block_code = clean_code(self.block_code, "Block Code", max_length=10)
