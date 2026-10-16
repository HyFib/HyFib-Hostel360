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
	set_mess_settings,
)
from hostel360.hostel_mess.utils import get_week_start
from hostel360.setup.roles import ADMIN_ROLE, MESS_MANAGER_ROLE, MESS_STAFF_ROLE, WARDEN_ROLE

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

	# ------------------------------------------------------------------ separation of duties

	def raise_indent_as(self, user, qty=80):
		with set_user(user):
			return self.make_indent(qty=qty)

	def test_requester_cannot_decide_their_own_indent(self):
		requester, colleague = make_user([MESS_MANAGER_ROLE]), make_user([MESS_MANAGER_ROLE])
		indent = self.raise_indent_as(requester)
		self.assertEqual(indent.requested_by, requester)
		with set_user(requester):
			with self.assertRaises(frappe.PermissionError):
				indent.approve()
			with self.assertRaises(frappe.PermissionError):
				indent.reject("Not needed")
		with set_user(colleague):
			indent.approve("Checked against the week's menu")
		self.assertEqual((indent.status, indent.approved_by), ("Approved", colleague))

	def test_administrators_may_decide_their_own_indent(self):
		admin = make_user([ADMIN_ROLE])
		indent = self.raise_indent_as(admin)
		with set_user(admin):
			indent.approve()
		self.assertEqual(indent.status, "Approved")

	def test_large_indents_need_a_warden(self):
		set_mess_settings(indent_warden_approval_above=5000)
		self.addCleanup(set_mess_settings, indent_warden_approval_above=0)
		indent = self.raise_indent_as(make_user([MESS_MANAGER_ROLE]))  # 80 kg x 110 = 8,800
		with set_user(make_user([MESS_MANAGER_ROLE])), self.assertRaises(frappe.PermissionError):
			indent.approve()
		with set_user(make_user([WARDEN_ROLE])):
			indent.approve()
		self.assertEqual(indent.status, "Approved")

	# ------------------------------------------------------------------ menu

	def test_indent_is_tied_to_the_menu_of_its_week(self):
		required_by = add_days(nowdate(), 3)
		menu = frappe.get_doc(
			{
				"doctype": "Mess Menu",
				"mess": self.mess,
				"week_start_date": get_week_start(required_by),
				"items": [{"menu_date": required_by, "meal": "Lunch", "dishes": "Dal fry, rice"}],
			}
		).insert(ignore_permissions=True)
		indent = self.make_indent(submit=False)
		self.assertEqual(indent.mess_menu, menu.name)
		preview = indent.get_menu_preview()
		self.assertEqual(preview["menu"], menu.name)
		self.assertEqual(preview["days"][0]["meals"][0]["dishes"], "Dal fry, rice")

		other_menu = frappe.get_doc(
			{
				"doctype": "Mess Menu",
				"mess": make_mess([make_hostel()]),
				"week_start_date": get_week_start(required_by),
				"items": [{"menu_date": required_by, "meal": "Lunch", "dishes": "Curd rice"}],
			}
		).insert(ignore_permissions=True)
		indent.mess_menu = other_menu.name
		with self.assertRaisesRegex(frappe.ValidationError, "belongs to"):
			indent.save()

	# ------------------------------------------------------------------ receipts against indents

	def test_receipts_can_be_required_to_follow_an_approved_indent(self):
		set_mess_settings(require_indent_for_receipts=1)
		self.addCleanup(set_mess_settings, require_indent_for_receipts=0)
		yesterday = add_days(nowdate(), -1)
		with self.assertRaisesRegex(frappe.ValidationError, "approved indent"):
			make_stock_entry(self.mess, "Receipt", [(self.dal, 10, 110)], yesterday)
		milk = make_item("Litre")
		frappe.db.set_value("Mess Item", milk, "is_perishable", 1)
		make_stock_entry(self.mess, "Receipt", [(milk, 20, 50)], yesterday)

		indent = self.make_indent(qty=30)
		indent.approve()
		with self.assertRaisesRegex(frappe.ValidationError, "approved quantity"):
			make_stock_entry(self.mess, "Receipt", [(self.dal, 31, 110)], yesterday, indent=indent.name)
		rice = make_item("Kg")
		with self.assertRaisesRegex(frappe.ValidationError, "not in indent"):
			make_stock_entry(
				self.mess, "Receipt", [(self.dal, 30, 110), (rice, 5, 40)], yesterday, indent=indent.name
			)
		receipt = make_stock_entry(
			self.mess, "Receipt", [(self.dal, 30, 110), (milk, 5, 50)], yesterday, indent=indent.name
		)
		self.assertEqual(receipt.docstatus, 1)
