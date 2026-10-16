"""Mess stock ledger with moving-average valuation per (mess, item).

Every submitted Mess Stock Entry writes one Mess Stock Ledger Entry per item row. The
ledger is ordered by posting date/time and then by insertion order (the ledger uses
autoincrement names), and each row carries the running balance quantity, value and
moving-average rate.

Back-dated entries are allowed: after rows are inserted (or cancelled), every later row
of the same (mess, item) is revalued and the values of later issues are written back to
their vouchers. Anything on or before the last published mess bill is closed (see
``utils.assert_period_open``), so revaluation never changes a published bill.

Concurrency: stock movements of an item are serialised by writing the item's
``stock_revision`` (an exclusive row lock held until commit), taken in item-name order to
avoid deadlocks. A lock alone is not enough: under REPEATABLE READ the waiting transaction
would go on reading the ledger as it was when its first query ran, without the movement it
waited for. Writing the row makes such a transaction fail instead (MariaDB's snapshot
isolation refuses to change a row changed after the snapshot), and the revision check below
catches the same situation on servers where snapshot isolation is off. Either way the user
is asked to submit again, and the next attempt reads the ledger afresh.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import cint, flt, format_date, get_link_to_form, get_time, getdate

LEDGER = "Mess Stock Ledger Entry"
VOUCHER_TYPE = "Mess Stock Entry"

QTY_PRECISION = 3
RATE_PRECISION = 6
VALUE_PRECISION = 2
QTY_TOLERANCE = 0.0005

INCOMING_TYPES = ("Receipt",)
OUTGOING_TYPES = ("Issue", "Return to Supplier")


class NegativeStockError(frappe.ValidationError):
	pass


class StockChangedError(frappe.ValidationError):
	"""Another posting of the item committed while this one was being prepared."""


def get_posting_datetime(posting_date, posting_time=None) -> datetime.datetime:
	return datetime.datetime.combine(getdate(posting_date), get_time(posting_time or "00:00:00"))


def signed_qty(entry_type: str, qty) -> float:
	"""Quantity change caused by a stock entry row."""
	qty = flt(qty, QTY_PRECISION)
	if entry_type in OUTGOING_TYPES:
		return -abs(qty)
	if entry_type in INCOMING_TYPES:
		return abs(qty)
	return qty  # Adjustment: signed as entered


def lock_items(items) -> None:
	"""Take the stock lock of each item and make sure this transaction sees its latest ledger.

	Raises StockChangedError when another posting of an item committed after this
	transaction started reading, because every balance it computed would be out of date.
	"""
	for item in sorted(set(items)):
		_lock_item(item)


def _lock_item(item: str) -> None:
	values = {"item": item}
	# What this transaction's snapshot believes; a plain read never waits and never fails.
	seen = frappe.db.sql("select stock_revision from `tabMess Item` where name = %(item)s", values)
	if not seen:
		return  # unknown items are reported by the entry's own validation
	try:
		frappe.db.sql(
			"update `tabMess Item` set stock_revision = ifnull(stock_revision, 0) + 1 where name = %(item)s",
			values,
		)
	except frappe.QueryDeadlockError:
		# MariaDB refuses to update a row changed after this transaction's snapshot
		# (innodb_snapshot_isolation), or picked this transaction as a deadlock victim.
		_raise_stock_changed(item)
	# A transaction always reads its own change, which is applied to the latest committed row,
	# so anything other than "what we saw plus one" means a posting committed in between.
	current = frappe.db.sql("select stock_revision from `tabMess Item` where name = %(item)s", values)
	if cint(current[0][0]) != cint(seen[0][0]) + 1:
		_raise_stock_changed(item)


def _raise_stock_changed(item: str) -> None:
	frappe.throw(
		_(
			"The stock of {0} changed while this entry was being saved, because another entry for it was posted at the same moment. Please submit it again."
		).format(frappe.bold(item)),
		StockChangedError,
		title=_("Stock Changed"),
	)


# ------------------------------------------------------------------ balances


# Upper bounds used when no "as of" point is given, so the balance queries stay static.
_END_OF_TIME = datetime.datetime(9999, 12, 31, 23, 59, 59)
# Larger than any autoincrement ledger name; "before (time, this)" then means "up to time".
_AFTER_ALL_ROWS = 2**62


def _as_of_bounds(as_of) -> dict:
	"""``{as_of_date, as_of_datetime}`` for a date (end of that day), a datetime or nothing."""
	if not as_of:
		return {"as_of_date": _END_OF_TIME.date(), "as_of_datetime": _END_OF_TIME}
	if isinstance(as_of, datetime.datetime) or (isinstance(as_of, str) and len(as_of.strip()) > 10):
		return {"as_of_date": _END_OF_TIME.date(), "as_of_datetime": frappe.utils.get_datetime(as_of)}
	return {"as_of_date": getdate(as_of), "as_of_datetime": _END_OF_TIME}


def get_stock_balance(mess: str, item: str, as_of=None) -> frappe._dict:
	"""Stock of an item in a mess: ``{qty, valuation_rate, stock_value}``.

	``as_of`` may be a date (end of that day) or a datetime; default is now.
	"""
	row = frappe.db.sql(
		"""
		select qty_after_transaction, valuation_rate, stock_value
		from `tabMess Stock Ledger Entry`
		where mess = %(mess)s and item = %(item)s and is_cancelled = 0
			and posting_date <= %(as_of_date)s and posting_datetime <= %(as_of_datetime)s
		order by posting_datetime desc, name desc
		limit 1
		""",
		{"mess": mess, "item": item, **_as_of_bounds(as_of)},
		as_dict=True,
	)
	if not row:
		return frappe._dict(qty=0.0, valuation_rate=0.0, stock_value=0.0)
	row = row[0]
	return frappe._dict(
		qty=flt(row.qty_after_transaction, QTY_PRECISION),
		valuation_rate=flt(row.valuation_rate, RATE_PRECISION),
		stock_value=flt(row.stock_value, VALUE_PRECISION),
	)


def get_balance_qty(mess: str, item: str, as_of=None) -> float:
	return get_stock_balance(mess, item, as_of).qty


def get_stock_levels(mess: str, as_of=None, include_inactive: bool = False) -> list[frappe._dict]:
	"""Balance of every item of a mess, with reorder information.

	Active items are always listed; inactive items only when they still hold stock or when
	asked. Reorder levels are set per item, so an item is flagged for reordering only in a
	mess that has stocked it before; a mess that never uses an item is not nagged about it.
	"""
	balances = {
		row.item: row
		for row in frappe.db.sql(
			"""
			select item, qty_after_transaction, valuation_rate, stock_value
			from (
				select item, qty_after_transaction, valuation_rate, stock_value,
					row_number() over (partition by item order by posting_datetime desc, name desc) as position
				from `tabMess Stock Ledger Entry`
				where mess = %(mess)s and is_cancelled = 0
					and posting_date <= %(as_of_date)s and posting_datetime <= %(as_of_datetime)s
			) latest
			where position = 1
			""",
			{"mess": mess, **_as_of_bounds(as_of)},
			as_dict=True,
		)
	}

	items = frappe.get_all(
		"Mess Item",
		fields=[
			"name",
			"item_name",
			"local_name",
			"category",
			"uom",
			"reorder_level",
			"reorder_qty",
			"is_active",
		],
		order_by="category asc, item_name asc",
	)
	levels = []
	for item in items:
		balance = balances.get(item.name)
		qty = flt(balance.qty_after_transaction, QTY_PRECISION) if balance else 0.0
		if not item.is_active and not include_inactive and not qty:
			continue
		reorder_level = flt(item.reorder_level, QTY_PRECISION)
		levels.append(
			frappe._dict(
				item=item.name,
				item_name=item.item_name,
				local_name=item.local_name,
				category=item.category,
				uom=item.uom,
				qty=qty,
				valuation_rate=flt(balance.valuation_rate, RATE_PRECISION) if balance else 0.0,
				stock_value=flt(balance.stock_value, VALUE_PRECISION) if balance else 0.0,
				reorder_level=reorder_level,
				reorder_qty=flt(item.reorder_qty, QTY_PRECISION),
				below_reorder=bool(balance and reorder_level and qty <= reorder_level),
				is_active=item.is_active,
				has_history=bool(balance),
			)
		)
	return levels


def suggest_reorder_qty(level: dict) -> float:
	"""Quantity to indent for an item at or below its reorder level."""
	if flt(level.get("reorder_qty")):
		return flt(level["reorder_qty"], QTY_PRECISION)
	return max(flt(2 * flt(level.get("reorder_level")) - flt(level.get("qty")), QTY_PRECISION), 0)


# ------------------------------------------------------------------ posting


def check_stock_availability(doc, cancelling: bool = False) -> None:
	"""Refuse a submit or cancel that would take any balance below zero, before writing.

	Locks the items first, so the check and the posting that follows in the same
	transaction see the same ledger.
	"""
	posting_datetime = get_posting_datetime(doc.posting_date, doc.posting_time)
	changes = {}
	for row in doc.items:
		changes.setdefault(row.item, []).append(signed_qty(doc.entry_type, row.qty))
	lock_items(changes)

	for item, deltas in changes.items():
		if cancelling:
			own = frappe.get_all(
				LEDGER,
				filters={
					"voucher_type": doc.doctype,
					"voucher_no": doc.name,
					"item": item,
					"is_cancelled": 0,
				},
				fields=["name", "posting_datetime"],
				order_by="name asc",
				limit=1,
			)
			if not own:
				continue
			start_datetime, start_name = own[0].posting_datetime, own[0].name
			opening = _qty_before(doc.mess, item, start_datetime, start_name)
			later = _rows_from(doc.mess, item, start_datetime, start_name, exclude_voucher=doc.name)
			_replay(doc.mess, item, opening, [], later, doc.name)
		else:
			opening = _qty_before(doc.mess, item, posting_datetime, None)
			later = _rows_from(doc.mess, item, posting_datetime, None)
			own_rows = [
				frappe._dict(
					actual_qty=qty,
					voucher_no=doc.name,
					voucher_type=doc.doctype,
					posting_date=doc.posting_date,
				)
				for qty in deltas
			]
			_replay(doc.mess, item, opening, own_rows, later, doc.name)


def _qty_before(mess, item, posting_datetime, name) -> float:
	"""Balance just before a ledger position; with no name, after everything at that time."""
	row = frappe.db.sql(
		"""
		select qty_after_transaction from `tabMess Stock Ledger Entry`
		where mess = %(mess)s and item = %(item)s and is_cancelled = 0
			and (posting_datetime < %(pdt)s or (posting_datetime = %(pdt)s and name < %(name)s))
		order by posting_datetime desc, name desc limit 1
		""",
		{
			"mess": mess,
			"item": item,
			"pdt": posting_datetime,
			"name": _AFTER_ALL_ROWS if name is None else name,
		},
	)
	return flt(row[0][0], QTY_PRECISION) if row else 0.0


def _rows_from(mess, item, posting_datetime, name, exclude_voucher: str | None = None):
	"""Ledger rows from a position onwards; with no name, only rows after that time."""
	return frappe.db.sql(
		"""
		select actual_qty, voucher_type, voucher_no, posting_date
		from `tabMess Stock Ledger Entry`
		where mess = %(mess)s and item = %(item)s and is_cancelled = 0
			and (posting_datetime > %(pdt)s or (posting_datetime = %(pdt)s and name >= %(name)s))
			and voucher_no != %(exclude)s
		order by posting_datetime asc, name asc
		""",
		{
			"mess": mess,
			"item": item,
			"pdt": posting_datetime,
			"name": _AFTER_ALL_ROWS if name is None else name,
			"exclude": exclude_voucher or "",
		},
		as_dict=True,
	)


def _replay(mess, item, opening, own_rows, later_rows, current_voucher) -> None:
	qty = opening
	for row in [*own_rows, *later_rows]:
		after = flt(qty + flt(row.actual_qty, QTY_PRECISION), QTY_PRECISION)
		if after < -QTY_TOLERANCE:
			_raise_negative_stock(mess, item, row, qty, current_voucher)
		qty = max(after, 0.0)


def post_stock_entry(doc) -> dict[str, tuple[float, float]]:
	"""Write ledger rows for a submitted Mess Stock Entry.

	Returns ``{row name: (rate, amount)}`` for the entry's outgoing rows, valued at the
	moving-average rate at the moment of posting.
	"""
	posting_datetime = get_posting_datetime(doc.posting_date, doc.posting_time)
	lock_items(row.item for row in doc.items)

	first_row_by_item = {}
	for row in doc.items:
		actual_qty = signed_qty(doc.entry_type, row.qty)
		ledger = frappe.get_doc(
			{
				"doctype": LEDGER,
				"mess": doc.mess,
				"item": row.item,
				"posting_date": getdate(doc.posting_date),
				"posting_time": get_time(doc.posting_time or "00:00:00"),
				"posting_datetime": posting_datetime,
				"voucher_type": doc.doctype,
				"voucher_no": doc.name,
				"voucher_detail_no": row.name,
				"entry_type": doc.entry_type,
				"actual_qty": actual_qty,
				"incoming_rate": flt(row.rate, RATE_PRECISION) if actual_qty > 0 else 0,
				"is_cancelled": 0,
			}
		)
		ledger.flags.from_stock_posting = True
		ledger.insert(ignore_permissions=True)
		first_row_by_item.setdefault(row.item, ledger.name)

	outgoing = {}
	for item, first_name in first_row_by_item.items():
		outgoing.update(revalue(doc.mess, item, posting_datetime, first_name, current_voucher=doc.name))
	return outgoing


def cancel_stock_entry(doc) -> None:
	"""Cancel the ledger rows of a stock entry and revalue everything after it.

	The original rows are flagged ``is_cancelled`` and mirrored by reversal rows (also
	flagged) so the ledger keeps a complete audit trail of what happened.
	"""
	rows = frappe.get_all(
		LEDGER,
		filters={"voucher_type": doc.doctype, "voucher_no": doc.name, "is_cancelled": 0},
		fields=[
			"name",
			"mess",
			"item",
			"posting_date",
			"posting_time",
			"posting_datetime",
			"voucher_detail_no",
			"entry_type",
			"actual_qty",
			"incoming_rate",
			"valuation_rate",
			"qty_after_transaction",
			"stock_value",
			"stock_value_difference",
		],
		order_by="name asc",
	)
	if not rows:
		return
	lock_items(row.item for row in rows)

	ledger_table = frappe.qb.DocType(LEDGER)
	(
		frappe.qb.update(ledger_table)
		.set(ledger_table.is_cancelled, 1)
		.where(ledger_table.name.isin([row.name for row in rows]))
	).run()

	first_row_by_item = {}
	for row in rows:
		first_row_by_item.setdefault(row.item, row)
		reversal = frappe.get_doc(
			{
				"doctype": LEDGER,
				"mess": row.mess,
				"item": row.item,
				"posting_date": row.posting_date,
				"posting_time": row.posting_time,
				"posting_datetime": row.posting_datetime,
				"voucher_type": doc.doctype,
				"voucher_no": doc.name,
				"voucher_detail_no": row.voucher_detail_no,
				"entry_type": row.entry_type,
				"actual_qty": -flt(row.actual_qty, QTY_PRECISION),
				"incoming_rate": row.incoming_rate,
				"valuation_rate": row.valuation_rate,
				"qty_after_transaction": row.qty_after_transaction,
				"stock_value": row.stock_value,
				"stock_value_difference": -flt(row.stock_value_difference, VALUE_PRECISION),
				"is_cancelled": 1,
			}
		)
		reversal.flags.from_stock_posting = True
		reversal.insert(ignore_permissions=True)

	for item, row in first_row_by_item.items():
		revalue(row.mess, item, row.posting_datetime, row.name, current_voucher=doc.name)


def revalue(mess: str, item: str, posting_datetime, from_name, current_voucher: str | None = None):
	"""Recompute running balances of an item from a ledger position onwards.

	The position is ``(posting_datetime, from_name)``: rows strictly before it give the
	opening state; it and every later non-cancelled row are recomputed. Raises
	NegativeStockError when any row would take the balance below zero.
	"""
	opening = frappe.db.sql(
		"""
		select qty_after_transaction, stock_value, valuation_rate
		from `tabMess Stock Ledger Entry`
		where mess = %(mess)s and item = %(item)s and is_cancelled = 0
			and (posting_datetime < %(posting_datetime)s
				or (posting_datetime = %(posting_datetime)s and name < %(from_name)s))
		order by posting_datetime desc, name desc
		limit 1
		""",
		{"mess": mess, "item": item, "posting_datetime": posting_datetime, "from_name": from_name},
		as_dict=True,
	)
	rows = frappe.db.sql(
		"""
		select name, posting_date, voucher_type, voucher_no, voucher_detail_no, actual_qty,
			incoming_rate, qty_after_transaction, valuation_rate, stock_value, stock_value_difference
		from `tabMess Stock Ledger Entry`
		where mess = %(mess)s and item = %(item)s and is_cancelled = 0
			and (posting_datetime > %(posting_datetime)s
				or (posting_datetime = %(posting_datetime)s and name >= %(from_name)s))
		order by posting_datetime asc, name asc
		""",
		{"mess": mess, "item": item, "posting_datetime": posting_datetime, "from_name": from_name},
		as_dict=True,
	)

	qty = flt(opening[0].qty_after_transaction, QTY_PRECISION) if opening else 0.0
	value = flt(opening[0].stock_value, VALUE_PRECISION) if opening else 0.0
	rate = flt(opening[0].valuation_rate, RATE_PRECISION) if opening else 0.0

	current_outgoing = {}
	voucher_updates = {}
	for row in rows:
		actual_qty = flt(row.actual_qty, QTY_PRECISION)
		if actual_qty >= 0:
			incoming_rate = flt(row.incoming_rate, RATE_PRECISION)
			difference = flt(actual_qty * incoming_rate, VALUE_PRECISION)
			qty = flt(qty + actual_qty, QTY_PRECISION)
			value = flt(value + difference, VALUE_PRECISION)
			rate = flt(value / qty, RATE_PRECISION) if qty > QTY_TOLERANCE else incoming_rate
		else:
			balance_after = flt(qty + actual_qty, QTY_PRECISION)
			if balance_after < -QTY_TOLERANCE:
				_raise_negative_stock(mess, item, row, qty, current_voucher)
			if balance_after <= QTY_TOLERANCE:
				# The last unit out carries whatever value is left, so rounding never
				# leaves value on an empty shelf.
				difference = -value
				balance_after = 0.0
			else:
				difference = flt(actual_qty * rate, VALUE_PRECISION)
			qty = balance_after
			value = flt(value + difference, VALUE_PRECISION)

			outgoing_amount = flt(-difference, VALUE_PRECISION)
			outgoing_rate = flt(outgoing_amount / abs(actual_qty), RATE_PRECISION)
			if row.voucher_type == VOUCHER_TYPE and row.voucher_detail_no:
				if row.voucher_no == current_voucher:
					current_outgoing[row.voucher_detail_no] = (outgoing_rate, outgoing_amount)
				else:
					voucher_updates.setdefault(row.voucher_no, {})[row.voucher_detail_no] = (
						outgoing_rate,
						outgoing_amount,
					)

		if (
			abs(flt(row.qty_after_transaction) - qty) > QTY_TOLERANCE / 10
			or abs(flt(row.stock_value) - value) >= 0.005
			or abs(flt(row.stock_value_difference) - difference) >= 0.005
			or abs(flt(row.valuation_rate) - rate) >= 0.000001
		):
			frappe.db.set_value(
				LEDGER,
				row.name,
				{
					"qty_after_transaction": qty,
					"valuation_rate": rate,
					"stock_value": value,
					"stock_value_difference": difference,
				},
				update_modified=False,
			)

	_write_back_outgoing_values(voucher_updates)
	return current_outgoing


def _write_back_outgoing_values(voucher_updates: dict) -> None:
	"""Store revalued issue rates on later stock entries and refresh their totals."""
	for voucher, rows in voucher_updates.items():
		changed = False
		for detail, (rate, amount) in rows.items():
			current = frappe.db.get_value("Mess Stock Entry Item", detail, ["rate", "amount"], as_dict=True)
			if not current:
				continue
			if abs(flt(current.amount) - amount) >= 0.005 or abs(flt(current.rate) - rate) >= 0.0001:
				frappe.db.set_value(
					"Mess Stock Entry Item",
					detail,
					{"rate": rate, "valuation_rate": rate, "amount": amount},
					update_modified=False,
				)
				changed = True
		if changed:
			total = frappe.db.sql(
				"""select coalesce(sum(amount), 0) from `tabMess Stock Entry Item`
				where parent = %(parent)s and parenttype = 'Mess Stock Entry'""",
				{"parent": voucher},
			)[0][0]
			frappe.db.set_value(
				VOUCHER_TYPE, voucher, "total_amount", flt(total, VALUE_PRECISION), update_modified=False
			)


def _raise_negative_stock(mess, item, row, available, current_voucher) -> None:
	uom = frappe.db.get_value("Mess Item", item, "uom") or ""
	needed = abs(flt(row.actual_qty, QTY_PRECISION))
	if row.voucher_no == current_voucher:
		message = _(
			"Only {0} {1} of {2} is in stock at {3} on {4}, but this entry takes out {5} {1}."
		).format(
			frappe.bold(flt(available, QTY_PRECISION)),
			uom,
			frappe.bold(item),
			mess,
			format_date(row.posting_date),
			frappe.bold(needed),
		)
	else:
		message = _(
			"This change would leave too little {0} at {1} for {2} dated {3}, which takes out {4} {5}. "
			"Post the receipt with an earlier date, or cancel the later entry first."
		).format(
			frappe.bold(item),
			mess,
			get_link_to_form(row.voucher_type, row.voucher_no),
			format_date(row.posting_date),
			needed,
			uom,
		)
	frappe.throw(message, NegativeStockError, title=_("Insufficient Stock"))


# ------------------------------------------------------------------ valuation helpers


def update_last_purchase_rate(items) -> None:
	"""Last purchase rate of each item, from its latest non-cancelled receipt in any mess."""
	for item in set(items):
		rate = frappe.db.sql(
			"""
			select incoming_rate from `tabMess Stock Ledger Entry`
			where item = %(item)s and is_cancelled = 0 and entry_type = 'Receipt'
			order by posting_datetime desc, name desc
			limit 1
			""",
			{"item": item},
		)
		frappe.db.set_value(
			"Mess Item",
			item,
			"last_purchase_rate",
			flt(rate[0][0], VALUE_PRECISION) if rate else 0,
			update_modified=False,
		)


def get_issue_value(mess: str, from_date, to_date) -> float:
	"""Value of provisions issued to the kitchen in a period (the dividing-system provision cost)."""
	value = frappe.db.sql(
		"""
		select coalesce(sum(-stock_value_difference), 0)
		from `tabMess Stock Ledger Entry`
		where mess = %(mess)s and is_cancelled = 0 and voucher_type = 'Mess Stock Entry'
			and entry_type = 'Issue' and posting_date between %(from_date)s and %(to_date)s
		""",
		{"mess": mess, "from_date": getdate(from_date), "to_date": getdate(to_date)},
	)[0][0]
	return flt(value, VALUE_PRECISION)
