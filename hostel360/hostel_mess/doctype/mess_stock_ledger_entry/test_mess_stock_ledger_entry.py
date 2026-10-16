# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from hostel360.hostel_mess.testing import (
	EXTERNAL_DEPENDENCIES,
	make_hostel,
	make_item,
	make_mess,
	make_stock_entry,
)

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


class TestMessStockLedgerEntry(IntegrationTestCase):
	def test_ledger_is_immutable(self):
		mess = make_mess([make_hostel()])
		item = make_item("Kg")
		entry = make_stock_entry(mess, "Receipt", [(item, 5, 20)], add_days(nowdate(), -1))
		name = frappe.get_all("Mess Stock Ledger Entry", filters={"voucher_no": entry.name}, pluck="name")[0]
		ledger = frappe.get_doc("Mess Stock Ledger Entry", name)
		ledger.actual_qty = 50
		with self.assertRaises(frappe.ValidationError):
			ledger.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			ledger.delete(ignore_permissions=True)
