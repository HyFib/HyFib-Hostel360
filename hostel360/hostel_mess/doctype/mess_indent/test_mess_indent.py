# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase, set_user
from frappe.utils import add_days, nowdate

from hostel360.hostel_mess.doctype.mess_indent.mess_indent import (
	get_items_below_reorder_level,
	make_stock_receipt,
)
from hostel360.hostel_mess.stock import get_stock_balance
from hostel360.hostel_mess.testing import (
	EXTERNAL_DEPENDENCIES,
	make_hostel,
	make_item,
	make_mess,
	make_stock_entry,
	make_user,
)
from hostel360.setup.roles import MESS_STAFF_ROLE

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


class TestMessIndent(IntegrationTestCase):
	def setUp(self):
		self.mess = make_mess([make_hostel()])
		self.dal = make_item("Kg", reorder_level=50)
		make_stock_entry(self.mess, "Receipt", [(self.dal, 20, 110)], add_days(nowdate(), -2))

	def make_indent(self, qty=80, submit=True):
		doc = frappe.get_doc(
			{
				"doctype": "Mess Indent",
				"mess": self.mess,
				"required_by": add_days(nowdate(), 3),
				"items": [{"item": self.dal, "required_qty": qty}],
			}
		).insert(ignore_permissions=True)
		if submit:
			doc.submit()
		return doc

	def test_items_below_reorder_level(self):
		suggestions = {row["item"]: row for row in get_items_below_reorder_level(self.mess)}
		self.assertIn(self.dal, suggestions)
		self.assertEqual(suggestions[self.dal]["current_stock"], 20)
		self.assertEqual(suggestions[self.dal]["required_qty"], 80)  # up to twice the reorder level
		self.assertEqual(suggestions[self.dal]["estimated_rate"], 110)

		frappe.db.set_value("Mess Item", self.dal, "reorder_qty", 100)
		suggestions = {row["item"]: row for row in get_items_below_reorder_level(self.mess)}
		self.assertEqual(suggestions[self.dal]["required_qty"], 100)

	def test_current_stock_and_estimates(self):
		indent = self.make_indent(submit=False)
		self.assertEqual(indent.status, "Draft")
		self.assertEqual(indent.items[0].current_stock, 20)
		self.assertEqual(indent.items[0].estimated_rate, 110)
		self.assertEqual(indent.total_estimated_amount, 8800)

	def test_approval_order_and_receipt(self):
		indent = self.make_indent()
		self.assertEqual(indent.status, "Pending Approval")

		indent.approve("Order from the usual dealer")
		self.assertEqual(indent.status, "Approved")
		self.assertEqual(indent.approved_by, "Administrator")

		indent.mark_ordered()
		self.assertEqual(indent.status, "Ordered")

		receipt = make_stock_receipt(indent.name)
		self.assertEqual(
			(receipt.entry_type, receipt.indent, receipt.mess), ("Receipt", indent.name, self.mess)
		)
		self.assertEqual(receipt.items[0].qty, 80)
		receipt.insert(ignore_permissions=True)
		receipt.submit()

		indent.reload()
		self.assertEqual((indent.status, indent.stock_entry), ("Received", receipt.name))
		self.assertEqual(get_stock_balance(self.mess, self.dal).qty, 100)

		receipt.cancel()
		indent.reload()
		self.assertEqual(indent.status, "Ordered")

	def test_rejection_and_permissions(self):
		indent = self.make_indent()
		with self.assertRaises(frappe.ValidationError):
			indent.reject("")
		staff = make_user([MESS_STAFF_ROLE])
		with set_user(staff), self.assertRaises(frappe.PermissionError):
			indent.approve()
		indent.reject("Stock is enough for this week")
		self.assertEqual(indent.status, "Rejected")
		with self.assertRaises(frappe.ValidationError):
			indent.approve()
		with self.assertRaises(frappe.ValidationError):
			make_stock_receipt(indent.name)

	def test_status_cannot_be_edited_directly(self):
		indent = self.make_indent()
		indent.status = "Approved"
		with self.assertRaises(frappe.ValidationError):
			indent.save()
