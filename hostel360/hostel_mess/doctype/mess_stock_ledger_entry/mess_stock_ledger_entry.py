# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class MessStockLedgerEntry(Document):
	"""Immutable stock log written only by Mess Stock Entry postings."""

	def before_insert(self):
		if not self.flags.from_stock_posting:
			frappe.throw(_("Stock ledger entries are created by submitting a Mess Stock Entry."))

	def validate(self):
		if not self.is_new():
			frappe.throw(_("Stock ledger entries cannot be edited."))

	def on_trash(self):
		frappe.throw(_("Stock ledger entries cannot be deleted. Cancel the stock entry instead."))


def on_doctype_update():
	frappe.db.add_index("Mess Stock Ledger Entry", ["mess", "item", "posting_datetime"])
	frappe.db.add_index("Mess Stock Ledger Entry", ["voucher_type", "voucher_no"])
