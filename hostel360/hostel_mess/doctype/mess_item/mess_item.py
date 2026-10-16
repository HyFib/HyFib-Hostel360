# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class MessItem(Document):
	def before_save(self):
		# The stock revision is written only by stock postings. A form opened before a posting
		# still carries the old number; keep the stored one so the counter never moves back.
		if not self.is_new():
			self.stock_revision = cint(
				frappe.db.get_value("Mess Item", self.name, "stock_revision", for_update=True)
			)

	def validate(self):
		self.item_name = (self.item_name or "").strip()
		if self.item_code:
			self.item_code = self.item_code.strip().upper()
		if flt(self.reorder_level) < 0 or flt(self.reorder_qty) < 0:
			frappe.throw(_("Reorder level and reorder quantity cannot be negative."))
		if not self.is_new() and self.has_value_changed("uom") and self.has_stock_movements():
			frappe.throw(
				_("The unit of {0} cannot be changed after stock has been recorded in {1}.").format(
					frappe.bold(self.name), frappe.bold(self.get_doc_before_save().uom)
				)
			)

	def has_stock_movements(self) -> bool:
		return bool(frappe.db.exists("Mess Stock Ledger Entry", {"item": self.name}))
