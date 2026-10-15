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


class TestMessItem(IntegrationTestCase):
	def test_unit_is_fixed_once_stock_exists(self):
		item = frappe.get_doc("Mess Item", make_item("Kg"))
		item.uom = "Gram"
		item.save()

		make_stock_entry(
			make_mess([make_hostel()]), "Receipt", [(item.name, 1000, 1)], add_days(nowdate(), -1)
		)
		item.reload()
		item.uom = "Kg"
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be changed"):
			item.save()

	def test_negative_reorder_level_rejected(self):
		item = frappe.get_doc("Mess Item", make_item("Kg"))
		item.reorder_level = -1
		with self.assertRaises(frappe.ValidationError):
			item.save()
