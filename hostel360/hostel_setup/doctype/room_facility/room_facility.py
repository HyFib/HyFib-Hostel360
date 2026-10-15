# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class RoomFacility(Document):
	def before_naming(self):
		self.facility_name = " ".join((self.facility_name or "").split())

	def validate(self):
		self.facility_name = " ".join((self.facility_name or "").split())
