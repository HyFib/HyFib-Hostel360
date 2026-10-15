# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from hostel360.hostel_operations import support


class SupportTicket(Document):
	def before_insert(self):
		support.before_insert(self)

	def validate(self):
		support.validate(self)

	def after_insert(self):
		support.after_insert(self)

	def on_update(self):
		support.on_update(self)

	@frappe.whitelist()
	def close_ticket(self):
		support.close_ticket(self)

	@frappe.whitelist()
	def reopen_ticket(self, reason: str):
		support.reopen_ticket(self, reason)
