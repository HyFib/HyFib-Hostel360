# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from hostel360.hostel_setup.utils import default_floor_label


class HostelFloor(Document):
	def validate(self):
		if not -3 <= cint(self.floor_number) <= 50:
			frappe.throw(_("Floor Number must be between -3 and 50."))
		self.hostel = frappe.db.get_value("Hostel Block", self.block, "hostel")
		self.floor_label = " ".join((self.floor_label or "").split()) or default_floor_label(cint(self.floor_number))
