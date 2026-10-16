# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Item-wise provisions issued to the kitchen, by day, month or year (tender clause 2.5)."""

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import add_days, add_months, date_diff, flt, format_date, get_first_day, getdate, nowdate

from hostel360.hostel_mess.permissions import assert_mess_access, get_permitted_messes

MAX_DAILY_COLUMNS = 62


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.from_date or get_first_day(nowdate()))
	to_date = getdate(filters.to_date or nowdate())
	granularity = filters.granularity or "Monthly"
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	if granularity == "Daily" and date_diff(to_date, from_date) + 1 > MAX_DAILY_COLUMNS:
		frappe.throw(
			_("Choose Monthly or Yearly for periods longer than {0} days.").format(MAX_DAILY_COLUMNS)
		)

	buckets = get_buckets(from_date, to_date, granularity)
	rows = get_issues(filters, from_date, to_date)

	items = {}
	totals_by_bucket = {key: 0.0 for key, _label in buckets}
	for row in rows:
		key = bucket_key(row.posting_date, granularity)
		entry = items.setdefault(
			row.item,
			{
				"item": row.item,
				"local_name": row.local_name,
				"category": row.category,
				"uom": row.uom,
				"total_qty": 0.0,
				"total_value": 0.0,
			},
		)
		entry[f"{key}_qty"] = flt(entry.get(f"{key}_qty")) + flt(row.qty)
		entry[f"{key}_value"] = flt(entry.get(f"{key}_value")) + flt(row.value)
		entry["total_qty"] += flt(row.qty)
		entry["total_value"] += flt(row.value)
		totals_by_bucket[key] = totals_by_bucket.get(key, 0) + flt(row.value)

	data = sorted(items.values(), key=lambda d: -d["total_value"])
	for entry in data:
		entry["total_qty"] = flt(entry["total_qty"], 3)
		entry["total_value"] = flt(entry["total_value"], 2)
		entry["average_rate"] = flt(entry["total_value"] / entry["total_qty"], 2) if entry["total_qty"] else 0

	return (
		get_columns(buckets),
		data,
		None,
		get_chart(buckets, totals_by_bucket, granularity),
		get_summary(data, from_date, to_date),
	)


def get_issues(filters, from_date, to_date):
	ledger = frappe.qb.DocType("Mess Stock Ledger Entry")
	item = frappe.qb.DocType("Mess Item")
	query = (
		frappe.qb.from_(ledger)
		.left_join(item)
		.on(item.name == ledger.item)
		.select(
			ledger.item,
			item.local_name,
			item.category,
			item.uom,
			ledger.posting_date,
			Sum(ledger.actual_qty * -1).as_("qty"),
			Sum(ledger.stock_value_difference * -1).as_("value"),
		)
		.where(ledger.is_cancelled == 0)
		.where(ledger.voucher_type == "Mess Stock Entry")
		.where(ledger.entry_type == "Issue")
		.where(ledger.posting_date[from_date:to_date])
		.groupby(ledger.item, item.local_name, item.category, item.uom, ledger.posting_date)
	)
	if filters.mess:
		assert_mess_access(filters.mess, doctype="Mess Stock Ledger Entry")
		query = query.where(ledger.mess == filters.mess)
	else:
		# Staff limited to some hostels see only the messes serving them.
		permitted = get_permitted_messes(doctype="Mess Stock Ledger Entry")
		if permitted is not None:
			if not permitted:
				return []
			query = query.where(ledger.mess.isin(sorted(permitted)))
	if filters.item:
		query = query.where(ledger.item == filters.item)
	if filters.category:
		query = query.where(item.category == filters.category)
	return query.run(as_dict=True)


def bucket_key(date, granularity: str) -> str:
	date = getdate(date)
	if granularity == "Daily":
		return date.strftime("d%Y%m%d")
	if granularity == "Yearly":
		return date.strftime("y%Y")
	return date.strftime("m%Y%m")


def get_buckets(from_date, to_date, granularity: str) -> list[tuple[str, str]]:
	buckets = []
	if granularity == "Daily":
		cursor = from_date
		while cursor <= to_date:
			buckets.append((bucket_key(cursor, granularity), format_date(cursor)))
			cursor = getdate(add_days(cursor, 1))
	elif granularity == "Yearly":
		for year in range(from_date.year, to_date.year + 1):
			buckets.append((f"y{year}", str(year)))
	else:
		cursor = from_date.replace(day=1)
		while cursor <= to_date:
			buckets.append((bucket_key(cursor, granularity), cursor.strftime("%b %Y")))
			cursor = getdate(add_months(cursor, 1))
	return buckets


def get_columns(buckets):
	columns = [
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Mess Item", "width": 200},
		{"fieldname": "local_name", "label": _("Local Name"), "fieldtype": "Data", "width": 140},
		{"fieldname": "category", "label": _("Category"), "fieldtype": "Data", "width": 110},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Data", "width": 70},
		{
			"fieldname": "total_qty",
			"label": _("Total Qty"),
			"fieldtype": "Float",
			"precision": 3,
			"width": 110,
		},
		{"fieldname": "total_value", "label": _("Total Value"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "average_rate", "label": _("Average Rate"), "fieldtype": "Currency", "width": 110},
	]
	for key, label in buckets:
		columns.append(
			{
				"fieldname": f"{key}_qty",
				"label": _("{0} Qty").format(label),
				"fieldtype": "Float",
				"precision": 3,
				"width": 110,
			}
		)
		columns.append(
			{
				"fieldname": f"{key}_value",
				"label": _("{0} Value").format(label),
				"fieldtype": "Currency",
				"width": 120,
			}
		)
	return columns


def get_chart(buckets, totals, granularity):
	if not any(totals.values()):
		return None
	return {
		"data": {
			"labels": [label for _key, label in buckets],
			"datasets": [
				{"name": _("Consumption Value"), "values": [flt(totals.get(key), 2) for key, _l in buckets]}
			],
		},
		"type": "line" if granularity == "Daily" else "bar",
		"fieldtype": "Currency",
		"colors": ["#2490ef"],
	}


def get_summary(data, from_date, to_date):
	total = sum(flt(d["total_value"]) for d in data)
	days = date_diff(to_date, from_date) + 1
	return [
		{"label": _("Consumption Value"), "value": total, "datatype": "Currency", "indicator": "Blue"},
		{
			"label": _("Average per Day"),
			"value": flt(total / days, 2) if days else 0,
			"datatype": "Currency",
			"indicator": "Blue",
		},
		{"label": _("Items Consumed"), "value": len(data), "datatype": "Int", "indicator": "Green"},
	]
