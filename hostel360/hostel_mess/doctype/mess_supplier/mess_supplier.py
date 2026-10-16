# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from hostel360.utils.validation import normalize_mobile, validate_gstin


class MessSupplier(Document):
	def validate(self):
		self.supplier_name = (self.supplier_name or "").strip()
		self.mobile_number = normalize_mobile(self.mobile_number)
		if self.gstin:
			self.gstin = self.gstin.strip().upper()
			validate_gstin(self.gstin)
		if self.email:
			self.email = self.email.strip().lower()
