# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from hostel360.hostel_mess.permissions import assert_mess_access
from hostel360.hostel_mess.stock import get_stock_levels


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.mess:
		frappe.throw(_("Select a mess."))
	assert_mess_access(filters.mess, doctype="Mess Stock Ledger Entry")
	as_on = getdate(filters.as_on_date or nowdate())

	levels = get_stock_levels(filters.mess, as_of=as_on, include_inactive=True)
	data = []
	for level in levels:
		if filters.category and level.category != filters.category:
			continue
		if cint(filters.below_reorder_only) and not level.below_reorder:
			continue
		if not cint(filters.show_zero_stock) and not level.qty and not level.below_reorder:
			continue
		data.append(
			{
				"item": level.item,
				"local_name": level.local_name,
				"category": level.category,
				"uom": level.uom,
				"qty": level.qty,
				"valuation_rate": flt(level.valuation_rate, 2),
				"stock_value": level.stock_value,
				"reorder_level": level.reorder_level,
				"stock_status": _("Reorder") if level.below_reorder else _("OK"),
			}
		)

	return get_columns(), data, None, get_chart(data), get_summary(data)


def get_columns():
	return [
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Mess Item", "width": 220},
		{"fieldname": "local_name", "label": _("Local Name"), "fieldtype": "Data", "width": 160},
		{"fieldname": "category", "label": _("Category"), "fieldtype": "Data", "width": 120},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Data", "width": 80},
		{"fieldname": "qty", "label": _("Balance Qty"), "fieldtype": "Float", "precision": 3, "width": 120},
		{"fieldname": "valuation_rate", "label": _("Valuation Rate"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "stock_value", "label": _("Stock Value"), "fieldtype": "Currency", "width": 140},
		{
			"fieldname": "reorder_level",
			"label": _("Reorder Level"),
			"fieldtype": "Float",
			"precision": 3,
			"width": 120,
		},
		{"fieldname": "stock_status", "label": _("Status"), "fieldtype": "Data", "width": 90},
	]


def get_chart(data):
	by_category = {}
	for row in data:
		by_category[row["category"]] = by_category.get(row["category"], 0) + flt(row["stock_value"])
	categories = [c for c, v in sorted(by_category.items(), key=lambda kv: -kv[1]) if v]
	if not categories:
		return None
	return {
		"data": {
			"labels": categories,
			"datasets": [{"name": _("Stock Value"), "values": [flt(by_category[c], 2) for c in categories]}],
		},
		"type": "bar",
		"fieldtype": "Currency",
		"colors": ["#2490ef"],
	}


def get_summary(data):
	total_value = sum(flt(row["stock_value"]) for row in data)
	low = sum(1 for row in data if row["stock_status"] == _("Reorder"))
	in_stock = sum(1 for row in data if flt(row["qty"]) > 0)
	return [
		{"label": _("Stock Value"), "value": total_value, "datatype": "Currency", "indicator": "Blue"},
		{"label": _("Items in Stock"), "value": in_stock, "datatype": "Int", "indicator": "Green"},
		{
			"label": _("At or Below Reorder Level"),
			"value": low,
			"datatype": "Int",
			"indicator": "Red" if low else "Green",
		},
	]
