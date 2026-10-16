# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, getdate, nowdate

from hostel360.hostel_mess.stock import (
	NegativeStockError,
	StockChangedError,
	get_stock_balance,
	get_stock_levels,
)
from hostel360.hostel_mess.testing import (
	EXTERNAL_DEPENDENCIES,
	make_hostel,
	make_item,
	make_mess,
	make_stock_entry,
	purge_committed_records,
)

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


def ledger_rows(voucher_no, cancelled=None):
	filters = {"voucher_no": voucher_no}
	if cancelled is not None:
		filters["is_cancelled"] = cancelled
	return frappe.get_all(
		"Mess Stock Ledger Entry",
		filters=filters,
		fields=["item", "actual_qty", "is_cancelled", "stock_value_difference", "qty_after_transaction"],
		order_by="name asc",
	)


class TestMessStockEntry(IntegrationTestCase):
	def setUp(self):
		self.mess = make_mess([make_hostel()])
		self.rice = make_item("Kg")
		self.day = lambda n: add_days(nowdate(), -n)

	def test_moving_average_valuation(self):
		make_stock_entry(self.mess, "Receipt", [(self.rice, 100, 40)], self.day(5))
		make_stock_entry(self.mess, "Receipt", [(self.rice, 50, 46)], self.day(4))

		balance = get_stock_balance(self.mess, self.rice)
		self.assertEqual(balance.qty, 150)
		self.assertEqual(balance.stock_value, 6300)
		self.assertEqual(balance.valuation_rate, 42)

		issue = make_stock_entry(self.mess, "Issue", [(self.rice, 30)], self.day(3))
		issue.reload()
		self.assertEqual(flt(issue.items[0].rate, 2), 42)
		self.assertEqual(flt(issue.items[0].amount, 2), 1260)
		self.assertEqual(flt(issue.total_amount, 2), 1260)

		balance = get_stock_balance(self.mess, self.rice)
		self.assertEqual(balance.qty, 120)
		self.assertEqual(balance.stock_value, 5040)
		self.assertEqual(balance.valuation_rate, 42)

		# Balance as of a date ignores later movements.
		self.assertEqual(get_stock_balance(self.mess, self.rice, self.day(5)).qty, 100)
		self.assertEqual(frappe.db.get_value("Mess Item", self.rice, "last_purchase_rate"), 46)

	def test_issuing_last_unit_clears_value(self):
		make_stock_entry(self.mess, "Receipt", [(self.rice, 3, 10)], self.day(3))
		make_stock_entry(self.mess, "Receipt", [(self.rice, 3, 11)], self.day(3))
		issue = make_stock_entry(self.mess, "Issue", [(self.rice, 6)], self.day(2))
		issue.reload()
		self.assertEqual(flt(issue.total_amount, 2), 63)
		balance = get_stock_balance(self.mess, self.rice)
		self.assertEqual((balance.qty, balance.stock_value), (0, 0))

	def test_issue_more_than_balance_is_rejected(self):
		make_stock_entry(self.mess, "Receipt", [(self.rice, 10, 50)], self.day(2))
		with self.assertRaises(NegativeStockError):
			make_stock_entry(self.mess, "Issue", [(self.rice, 10.5)], self.day(1))

		# Two rows of the same item count together.
		with self.assertRaises(NegativeStockError):
			make_stock_entry(self.mess, "Issue", [(self.rice, 6), (self.rice, 6)], self.day(1))

		self.assertEqual(get_stock_balance(self.mess, self.rice).qty, 10)

	def test_draft_shows_available_quantity(self):
		make_stock_entry(self.mess, "Receipt", [(self.rice, 25, 30)], self.day(2))
		draft = make_stock_entry(self.mess, "Issue", [(self.rice, 5)], self.day(1), submit=False)
		self.assertEqual(draft.items[0].available_qty, 25)
		self.assertEqual(flt(draft.items[0].rate), 30)
		self.assertEqual(flt(draft.total_amount), 150)

	def test_cancel_writes_reversal_rows_and_restores_balance(self):
		make_stock_entry(self.mess, "Receipt", [(self.rice, 40, 25)], self.day(3))
		issue = make_stock_entry(self.mess, "Issue", [(self.rice, 15)], self.day(2))
		self.assertEqual(get_stock_balance(self.mess, self.rice).qty, 25)

		issue.cancel()
		rows = ledger_rows(issue.name)
		self.assertEqual(len(rows), 2)
		self.assertTrue(all(row.is_cancelled for row in rows))
		self.assertEqual(sorted(row.actual_qty for row in rows), [-15, 15])

		balance = get_stock_balance(self.mess, self.rice)
		self.assertEqual(balance.qty, 40)
		self.assertEqual(balance.stock_value, 1000)

	def test_cancelling_consumed_receipt_is_blocked(self):
		receipt = make_stock_entry(self.mess, "Receipt", [(self.rice, 20, 25)], self.day(3))
		make_stock_entry(self.mess, "Issue", [(self.rice, 15)], self.day(2))
		with self.assertRaises(NegativeStockError):
			receipt.cancel()
		self.assertEqual(get_stock_balance(self.mess, self.rice).qty, 5)

	def test_backdated_receipt_revalues_later_issue(self):
		make_stock_entry(self.mess, "Receipt", [(self.rice, 10, 50)], self.day(5))
		issue = make_stock_entry(self.mess, "Issue", [(self.rice, 10)], self.day(3))
		self.assertEqual(flt(frappe.db.get_value("Mess Stock Entry", issue.name, "total_amount")), 500)

		# A receipt recorded late but dated before the issue changes the issue's cost.
		make_stock_entry(self.mess, "Receipt", [(self.rice, 10, 70)], self.day(4))
		issue.reload()
		self.assertEqual(flt(issue.items[0].rate, 2), 60)
		self.assertEqual(flt(issue.total_amount, 2), 600)

		balance = get_stock_balance(self.mess, self.rice)
		self.assertEqual(balance.qty, 10)
		self.assertEqual(balance.stock_value, 600)

	def test_backdated_issue_cannot_starve_a_later_issue(self):
		make_stock_entry(self.mess, "Receipt", [(self.rice, 10, 50)], self.day(5))
		make_stock_entry(self.mess, "Issue", [(self.rice, 8)], self.day(2))
		with self.assertRaises(NegativeStockError):
			make_stock_entry(self.mess, "Issue", [(self.rice, 5)], self.day(4))

	def test_adjustments(self):
		make_stock_entry(self.mess, "Receipt", [(self.rice, 10, 40)], self.day(3))
		surplus = make_stock_entry(self.mess, "Adjustment", [(self.rice, 2)], self.day(2))
		self.assertEqual(flt(surplus.items[0].rate), 40)
		make_stock_entry(self.mess, "Adjustment", [(self.rice, -3)], self.day(1))
		balance = get_stock_balance(self.mess, self.rice)
		self.assertEqual(balance.qty, 9)
		self.assertEqual(balance.stock_value, 360)

		new_item = make_item("Kg")
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry(self.mess, "Adjustment", [(new_item, 1)], self.day(1))

	def test_receipt_validation(self):
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry(self.mess, "Receipt", [(self.rice, 5, 0)], self.day(1))
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry(self.mess, "Receipt", [(self.rice, 5, 10)], add_days(nowdate(), 1))
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry(self.mess, "Return to Supplier", [(self.rice, 1)], self.day(1))

	def test_entries_in_published_billing_period_are_blocked(self):
		make_stock_entry(self.mess, "Receipt", [(self.rice, 10, 40)], self.day(10))
		with patch("hostel360.hostel_mess.utils.get_published_upto", return_value=getdate(self.day(5))):
			with self.assertRaises(frappe.ValidationError):
				make_stock_entry(self.mess, "Issue", [(self.rice, 1)], self.day(6))
			make_stock_entry(self.mess, "Issue", [(self.rice, 1)], self.day(4))

	def test_stock_levels_flag_reorder(self):
		dal = make_item("Kg", reorder_level=50)
		make_stock_entry(self.mess, "Receipt", [(dal, 20, 110), (self.rice, 5, 40)], self.day(2))
		levels = {row.item: row for row in get_stock_levels(self.mess)}
		self.assertTrue(levels[dal].below_reorder)
		self.assertEqual(levels[dal].qty, 20)
		self.assertFalse(levels[self.rice].below_reorder)

	def test_ledger_rows_cannot_be_created_directly(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Mess Stock Ledger Entry",
					"mess": self.mess,
					"item": self.rice,
					"posting_date": nowdate(),
					"actual_qty": 100,
				}
			).insert(ignore_permissions=True)


def has_snapshot_isolation_setting() -> bool:
	return bool(frappe.db.sql("show variables like 'innodb_snapshot_isolation'"))


class TestConcurrentStockPosting(IntegrationTestCase):
	"""Two people posting the same item at the same moment, each on their own connection.

	The fixtures are committed so that the second connection can see them, and purged after
	each test.
	"""

	def setUp(self):
		self.hostel = make_hostel()
		self.mess = make_mess([self.hostel])
		self.rice = make_item("Kg")
		day = add_days(nowdate(), -1)
		self.receipt = make_stock_entry(self.mess, "Receipt", [(self.rice, 10, 50)], day, "09:00:00").name
		self.first = make_stock_entry(self.mess, "Issue", [(self.rice, 8)], day, submit=False).name
		self.second = make_stock_entry(self.mess, "Issue", [(self.rice, 8)], day, submit=False).name
		frappe.db.commit()
		self.addCleanup(purge_committed_records, messes=[self.mess], items=[self.rice], hostels=[self.hostel])

	def open_late_request(self, doc_name, snapshot_isolation=True):
		"""Load a document on the second connection the way a request would before submitting."""
		with self.secondary_connection():
			if not snapshot_isolation:
				frappe.db.sql("set session innodb_snapshot_isolation = OFF")
			doc = frappe.get_doc("Mess Stock Entry", doc_name)
			self.assertEqual(get_stock_balance(self.mess, self.rice).qty, 10)
		return doc

	def submit_first_issue(self):
		with self.primary_connection():
			frappe.get_doc("Mess Stock Entry", self.first).submit()
			frappe.db.commit()

	def assert_late_submit_refused(self, late):
		with self.secondary_connection():
			try:
				with self.assertRaises(StockChangedError):
					late.submit()
			finally:
				frappe.db.rollback()
			# Submitting again reads the ledger afresh and sees what is really left.
			try:
				with self.assertRaises(NegativeStockError):
					frappe.get_doc("Mess Stock Entry", self.second).submit()
			finally:
				frappe.db.rollback()
		with self.primary_connection():
			self.assertEqual(get_stock_balance(self.mess, self.rice).qty, 2)
			self.assertEqual(frappe.db.get_value("Mess Stock Entry", self.second, "docstatus"), 0)

	def test_concurrent_issues_cannot_both_take_the_same_stock(self):
		late = self.open_late_request(self.second)
		self.submit_first_issue()
		self.assert_late_submit_refused(late)

	def test_stale_snapshot_is_caught_without_snapshot_isolation(self):
		if not has_snapshot_isolation_setting():
			self.skipTest("The database server has no innodb_snapshot_isolation setting")
		late = self.open_late_request(self.second, snapshot_isolation=False)
		self.submit_first_issue()
		self.assert_late_submit_refused(late)

	def test_cancelling_a_receipt_consumed_meanwhile_is_refused(self):
		with self.secondary_connection():
			receipt = frappe.get_doc("Mess Stock Entry", self.receipt)
			self.assertEqual(get_stock_balance(self.mess, self.rice).qty, 10)
		self.submit_first_issue()
		with self.secondary_connection():
			try:
				with self.assertRaises(StockChangedError):
					receipt.cancel()
			finally:
				frappe.db.rollback()
		with self.primary_connection():
			self.assertEqual(frappe.db.get_value("Mess Stock Entry", self.receipt, "docstatus"), 1)
			self.assertEqual(get_stock_balance(self.mess, self.rice).qty, 2)

	def test_unrelated_reads_do_not_block_a_posting(self):
		late = self.open_late_request(self.second)
		with self.secondary_connection():
			try:
				late.submit()
				self.assertEqual(get_stock_balance(self.mess, self.rice).qty, 2)
			finally:
				frappe.db.rollback()
