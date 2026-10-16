# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt, format_date, getdate, now_datetime, nowdate

from hostel360.hostel_mess.permissions import assert_mess_access
from hostel360.hostel_mess.stock import (
	QTY_PRECISION,
	VALUE_PRECISION,
	get_stock_balance,
	get_stock_levels,
	suggest_reorder_qty,
)
from hostel360.hostel_mess.utils import MEAL_ORDER, get_mess_settings, get_week_start
from hostel360.setup.roles import ADMIN_ROLES, MESS_MANAGER_ROLE, WARDEN_ROLE
from hostel360.utils.context import has_any_role
from hostel360.utils.jinja import format_inr

INDENT_APPROVER_ROLES = frozenset(ADMIN_ROLES | {MESS_MANAGER_ROLE, WARDEN_ROLE})
INDENT_ORDER_ROLES = frozenset(ADMIN_ROLES | {MESS_MANAGER_ROLE})
# Roles that may decide an indent they raised themselves, and indents above the configured limit.
INDENT_SENIOR_APPROVER_ROLES = frozenset(ADMIN_ROLES | {WARDEN_ROLE})


class MessIndent(Document):
	def validate(self):
		assert_mess_access(self.mess, doctype=self.doctype)
		if self.docstatus == 0:
			self.status = "Draft"
		if self.required_by and getdate(self.required_by) < getdate(self.posting_date):
			frappe.throw(_("Required By cannot be before the indent date."))
		self.set_menu()
		self.validate_items()
		self.set_stock_and_estimates()

	def on_submit(self):
		self.db_set({"status": "Pending Approval", "requested_by": frappe.session.user})

	def set_menu(self):
		"""Tie the indent to the menu of the week it is for (the week of Required By)."""
		if self.docstatus != 0:
			return
		if self.mess_menu:
			menu_mess = frappe.db.get_value("Mess Menu", self.mess_menu, "mess")
			if menu_mess != self.mess:
				frappe.throw(
					_("Menu {0} belongs to {1}, not to {2}.").format(
						frappe.bold(self.mess_menu), menu_mess, frappe.bold(self.mess)
					)
				)
			return
		week_start = get_week_start(self.required_by or self.posting_date)
		self.mess_menu = frappe.db.get_value(
			"Mess Menu", {"mess": self.mess, "week_start_date": week_start}, "name"
		)

	def before_update_after_submit(self):
		if not self.flags.status_update:
			before = self.get_doc_before_save()
			if before and before.status != self.status:
				frappe.throw(_("Use the Approve, Reject or Mark Ordered actions to change the status."))

	def on_cancel(self):
		if self.status == "Received":
			frappe.throw(_("Cancel the receipt {0} before cancelling this indent.").format(self.stock_entry))
		self.db_set("status", "Cancelled")

	def validate_items(self):
		if not self.items:
			frappe.throw(_("Add at least one item."))
		seen = set()
		for row in self.items:
			if row.item in seen:
				frappe.throw(
					_("Row {0}: {1} is listed more than once.").format(row.idx, frappe.bold(row.item))
				)
			seen.add(row.item)
			row.required_qty = flt(row.required_qty, QTY_PRECISION)
			if row.required_qty <= 0:
				frappe.throw(_("Row {0}: required quantity must be greater than zero.").format(row.idx))
			if flt(row.estimated_rate) < 0:
				frappe.throw(_("Row {0}: estimated rate cannot be negative.").format(row.idx))

	def set_stock_and_estimates(self):
		details = {
			d.name: d
			for d in frappe.get_all(
				"Mess Item",
				filters={"name": ["in", [row.item for row in self.items]]},
				fields=["name", "uom", "last_purchase_rate"],
			)
		}
		for row in self.items:
			item = details.get(row.item) or frappe._dict()
			row.uom = item.uom
			if self.docstatus == 0:
				balance = get_stock_balance(self.mess, row.item)
				row.current_stock = balance.qty
				if not flt(row.estimated_rate):
					row.estimated_rate = flt(item.last_purchase_rate) or flt(balance.valuation_rate, 2)
			row.estimated_amount = flt(flt(row.required_qty) * flt(row.estimated_rate), VALUE_PRECISION)
		self.total_estimated_amount = flt(
			sum(flt(row.estimated_amount) for row in self.items), VALUE_PRECISION
		)

	# ------------------------------------------------------------------ actions

	def _set_status(self, status: str, **values):
		self.status = status
		self.update(values)
		self.flags.status_update = True
		self.save(ignore_permissions=True)

	@frappe.whitelist(methods=["POST"])
	def approve(self, remarks: str | None = None):
		self._check_decision_allowed()
		self._set_status(
			"Approved",
			approved_by=frappe.session.user,
			approved_on=now_datetime(),
			approval_remarks=(remarks or "").strip() or None,
		)

	@frappe.whitelist(methods=["POST"])
	def reject(self, remarks: str | None = None):
		self._check_decision_allowed()
		remarks = (remarks or "").strip()
		if not remarks:
			frappe.throw(_("Please give a reason for rejecting the indent."))
		self._set_status(
			"Rejected",
			approved_by=frappe.session.user,
			approved_on=now_datetime(),
			approval_remarks=remarks,
		)

	@frappe.whitelist(methods=["POST"])
	def mark_ordered(self, supplier: str | None = None):
		if not has_any_role(INDENT_ORDER_ROLES):
			frappe.throw(_("Only a Mess Manager can place orders."), frappe.PermissionError)
		if self.docstatus != 1 or self.status != "Approved":
			frappe.throw(_("Only an approved indent can be marked as ordered."))
		values = {"ordered_on": getdate(nowdate())}
		if supplier:
			if not frappe.db.get_value("Mess Supplier", supplier, "is_active"):
				frappe.throw(_("Supplier {0} is not active.").format(frappe.bold(supplier)))
			values["supplier"] = supplier
		self._set_status("Ordered", **values)

	def _check_decision_allowed(self):
		if not has_any_role(INDENT_APPROVER_ROLES):
			frappe.throw(_("You are not permitted to approve indents."), frappe.PermissionError)
		if self.docstatus != 1 or self.status != "Pending Approval":
			frappe.throw(_("This indent is not awaiting approval."))
		assert_mess_access(self.mess, doctype=self.doctype)
		if has_any_role(INDENT_SENIOR_APPROVER_ROLES):
			return
		# Separation of duties: the person who raises an indent does not also approve it.
		if frappe.session.user in {self.owner, self.requested_by}:
			frappe.throw(
				_("You raised this indent, so someone else must approve or reject it."),
				frappe.PermissionError,
				title=_("Not Permitted"),
			)
		limit = flt(get_mess_settings().indent_warden_approval_above)
		if limit and flt(self.total_estimated_amount) > limit:
			frappe.throw(
				_(
					"Indents estimated above {0} must be approved by a Hostel Warden or Hostel Administrator."
				).format(format_inr(limit)),
				frappe.PermissionError,
				title=_("Warden Approval Needed"),
			)

	@frappe.whitelist()
	def get_menu_preview(self) -> dict | None:
		"""The planned meals of the linked menu, for the approver to compare quantities with."""
		if not self.mess_menu:
			return None
		menu = frappe.get_doc("Mess Menu", self.mess_menu)
		menu.check_permission("read")
		days = {}
		for row in menu.items:
			days.setdefault(str(getdate(row.menu_date)), []).append(row)
		return {
			"menu": menu.name,
			"status": menu.status,
			"week": _("{0} to {1}").format(
				format_date(menu.week_start_date), format_date(menu.week_end_date)
			),
			"days": [
				{
					"date": date,
					"label": format_date(date, "EEE d MMM"),
					"meals": [
						{"meal": row.meal, "dishes": row.dishes, "is_special": row.is_special}
						for row in sorted(
							rows, key=lambda r: MEAL_ORDER.index(r.meal) if r.meal in MEAL_ORDER else 9
						)
					],
				}
				for date, rows in sorted(days.items())
			],
		}


@frappe.whitelist()
def get_items_below_reorder_level(mess: str) -> list[dict]:
	"""Items at or below their reorder level, with a suggested quantity to indent."""
	frappe.has_permission("Mess Indent", "create", throw=True)
	assert_mess_access(mess, doctype="Mess Indent")
	rows = []
	for level in get_stock_levels(mess):
		if not level.below_reorder or not level.is_active:
			continue
		last_purchase_rate = frappe.db.get_value("Mess Item", level.item, "last_purchase_rate")
		rows.append(
			{
				"item": level.item,
				"uom": level.uom,
				"current_stock": level.qty,
				"required_qty": suggest_reorder_qty(level) or flt(level.reorder_level, QTY_PRECISION),
				"estimated_rate": flt(last_purchase_rate) or flt(level.valuation_rate, 2),
			}
		)
	return rows


@frappe.whitelist()
def make_stock_receipt(source_name: str, target_doc=None):
	"""Draft goods receipt for an approved or ordered indent."""
	frappe.has_permission("Mess Stock Entry", "create", throw=True)
	indent = frappe.db.get_value("Mess Indent", source_name, ["docstatus", "status"], as_dict=True)
	if not indent or indent.docstatus != 1 or indent.status not in ("Approved", "Ordered"):
		frappe.throw(_("Goods can be received only against an approved or ordered indent."))

	def set_missing_values(source, target):
		target.entry_type = "Receipt"
		target.indent = source.name
		target.posting_date = nowdate()

	return get_mapped_doc(
		"Mess Indent",
		source_name,
		{
			"Mess Indent": {
				"doctype": "Mess Stock Entry",
				"field_map": {"mess": "mess", "supplier": "supplier"},
				"field_no_map": ["naming_series", "posting_date", "status", "remarks"],
			},
			"Mess Indent Item": {
				"doctype": "Mess Stock Entry Item",
				"field_map": {"item": "item", "required_qty": "qty", "estimated_rate": "rate"},
				"field_no_map": ["amount", "current_stock"],
			},
		},
		target_doc,
		set_missing_values,
	)
