# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

from hostel360.hostel_mess.stock import (
	QTY_PRECISION,
	QTY_TOLERANCE,
	RATE_PRECISION,
	VALUE_PRECISION,
	cancel_stock_entry,
	check_stock_availability,
	get_posting_datetime,
	get_stock_balance,
	post_stock_entry,
	signed_qty,
	update_last_purchase_rate,
)
from hostel360.hostel_mess.utils import assert_period_open, in_migration


class MessStockEntry(Document):
	def validate(self):
		self.validate_mess()
		self.validate_posting_date()
		self.validate_supplier_and_indent()
		self.validate_items()
		self.set_balances_and_values()
		self.set_total()
		if self.docstatus == 0:
			self.warn_shortage()

	def before_submit(self):
		assert_period_open(self.mess, self.posting_date, _("Stock entries"))
		check_stock_availability(self)

	def on_submit(self):
		outgoing = post_stock_entry(self)
		for row in self.items:
			if row.name in outgoing:
				rate, amount = outgoing[row.name]
				row.db_set({"rate": rate, "valuation_rate": rate, "amount": amount}, update_modified=False)
		self.set_total()
		self.db_set("total_amount", self.total_amount, update_modified=False)

		if self.entry_type == "Receipt":
			update_last_purchase_rate(row.item for row in self.items)
			self.update_indent()

	def before_cancel(self):
		assert_period_open(self.mess, self.posting_date, _("Stock entries"))
		check_stock_availability(self, cancelling=True)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("Mess Stock Ledger Entry", "Mess Indent")
		cancel_stock_entry(self)
		if self.entry_type == "Receipt":
			update_last_purchase_rate(row.item for row in self.items)
			self.update_indent(cancelled=True)

	# ------------------------------------------------------------------ validation

	def validate_mess(self):
		if not frappe.db.get_value("Hostel Mess", self.mess, "is_active") and not in_migration():
			frappe.throw(_("Mess {0} is not active.").format(frappe.bold(self.mess)))

	def validate_posting_date(self):
		if not in_migration() and getdate(self.posting_date) > getdate(nowdate()):
			frappe.throw(_("Posting date cannot be in the future."))
		if self.is_new() or self.has_value_changed("posting_date"):
			assert_period_open(self.mess, self.posting_date, _("Stock entries"))

	def validate_supplier_and_indent(self):
		if self.entry_type not in ("Receipt", "Return to Supplier"):
			self.supplier = None
			self.supplier_invoice_no = None
			self.supplier_invoice_date = None
		if self.entry_type == "Return to Supplier" and not self.supplier:
			frappe.throw(_("Select the supplier the goods are returned to."))
		if self.entry_type != "Receipt":
			self.indent = None
		if not self.indent:
			return
		indent = frappe.db.get_value(
			"Mess Indent", self.indent, ["docstatus", "status", "mess"], as_dict=True
		)
		if not indent or indent.docstatus != 1 or indent.status not in ("Approved", "Ordered"):
			frappe.throw(
				_("Indent {0} must be approved (or ordered) before goods can be received against it.").format(
					frappe.bold(self.indent)
				)
			)
		if indent.mess != self.mess:
			frappe.throw(_("Indent {0} was raised for {1}.").format(frappe.bold(self.indent), indent.mess))

	def validate_items(self):
		if not self.items:
			frappe.throw(_("Add at least one item."))

		item_details = {
			d.name: d
			for d in frappe.get_all(
				"Mess Item",
				filters={"name": ["in", list({row.item for row in self.items})]},
				fields=["name", "uom", "is_active"],
			)
		}
		for row in self.items:
			item = item_details.get(row.item)
			if not item:
				frappe.throw(_("Row {0}: item {1} does not exist.").format(row.idx, row.item))
			row.uom = item.uom
			row.qty = flt(row.qty, QTY_PRECISION)

			if self.entry_type == "Adjustment":
				if abs(row.qty) < QTY_TOLERANCE:
					frappe.throw(
						_("Row {0}: enter the quantity to add (or a negative quantity to remove).").format(
							row.idx
						)
					)
			elif row.qty <= 0:
				frappe.throw(_("Row {0}: quantity must be greater than zero.").format(row.idx))

			if self.entry_type == "Receipt":
				if not item.is_active and not in_migration():
					frappe.throw(
						_("Row {0}: {1} is inactive and cannot be received.").format(
							row.idx, frappe.bold(row.item)
						)
					)
				if flt(row.rate) <= 0:
					frappe.throw(
						_("Row {0}: enter the purchase rate of {1}.").format(row.idx, frappe.bold(row.item))
					)
			elif flt(row.rate) < 0:
				frappe.throw(_("Row {0}: rate cannot be negative.").format(row.idx))

	def set_balances_and_values(self):
		"""Show stock on hand and preview values at the posting time.

		Outgoing rows are valued at the moving-average rate; the final value is fixed by the
		ledger on submit.
		"""
		if self.docstatus != 0:
			return
		posting_datetime = get_posting_datetime(self.posting_date, self.posting_time)
		balances = {}
		for row in self.items:
			if row.item not in balances:
				balances[row.item] = get_stock_balance(self.mess, row.item, posting_datetime)
			balance = balances[row.item]
			row.available_qty = balance.qty
			row.valuation_rate = balance.valuation_rate

			actual_qty = signed_qty(self.entry_type, row.qty)
			if actual_qty < 0:
				row.rate = flt(balance.valuation_rate, RATE_PRECISION)
			elif self.entry_type == "Adjustment" and flt(row.rate) <= 0:
				if not balance.valuation_rate:
					frappe.throw(
						_(
							"Row {0}: {1} has no valuation yet. Enter the rate for the quantity being added."
						).format(row.idx, frappe.bold(row.item))
					)
				row.rate = flt(balance.valuation_rate, RATE_PRECISION)
			row.amount = flt(abs(actual_qty) * flt(row.rate), VALUE_PRECISION)

	def set_total(self):
		self.total_amount = flt(sum(flt(row.amount) for row in self.items), VALUE_PRECISION)

	def warn_shortage(self):
		"""Drafts may be saved before the goods arrive; submit enforces the balance."""
		needed = {}
		for row in self.items:
			actual_qty = signed_qty(self.entry_type, row.qty)
			if actual_qty < 0:
				needed[row.item] = needed.get(row.item, 0) + abs(actual_qty)
		short = []
		for row in self.items:
			if row.item in needed and needed[row.item] - flt(row.available_qty) > QTY_TOLERANCE:
				short.append(
					_("{0}: {1} {2} needed, {3} {2} in stock").format(
						frappe.bold(row.item),
						flt(needed.pop(row.item), QTY_PRECISION),
						row.uom,
						flt(row.available_qty),
					)
				)
		if short:
			frappe.msgprint(
				"<br>".join(short),
				title=_("Not Enough Stock Yet"),
				indicator="orange",
				alert=False,
			)

	# ------------------------------------------------------------------ indent

	def update_indent(self, cancelled: bool = False):
		if not self.indent:
			return
		indent = frappe.get_doc("Mess Indent", self.indent)
		if cancelled:
			if indent.stock_entry != self.name:
				return
			indent.status = "Ordered" if indent.ordered_on else "Approved"
			indent.stock_entry = None
			indent.received_on = None
		else:
			indent.status = "Received"
			indent.stock_entry = self.name
			indent.received_on = getdate(self.posting_date)
		indent.flags.ignore_permissions = True
		indent.flags.status_update = True
		indent.save()


@frappe.whitelist()
def get_item_details(
	mess: str, item: str, posting_date: str | None = None, posting_time: str | None = None
) -> dict:
	"""Stock on hand, moving-average rate and last purchase rate of an item (form helper)."""
	frappe.has_permission("Mess Stock Entry", "read", throw=True)
	as_of = get_posting_datetime(posting_date or nowdate(), posting_time or "23:59:59") if mess else None
	balance = get_stock_balance(mess, item, as_of) if mess and item else frappe._dict(qty=0, valuation_rate=0)
	details = frappe.db.get_value("Mess Item", item, ["uom", "last_purchase_rate"], as_dict=True) or {}
	return {
		"available_qty": balance.qty,
		"valuation_rate": balance.valuation_rate,
		"uom": details.get("uom"),
		"last_purchase_rate": flt(details.get("last_purchase_rate")),
	}
