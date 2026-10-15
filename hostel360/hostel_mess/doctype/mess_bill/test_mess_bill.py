# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_mess.testing import EXTERNAL_DEPENDENCIES

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


class TestMessBill(IntegrationTestCase):
	def test_bills_are_created_only_by_a_bill_run(self):
		with self.assertRaisesRegex(frappe.ValidationError, "computing a Mess Bill Run"):
			bill = frappe.get_doc(
				{"doctype": "Mess Bill", "mess_bill_run": "X", "student": "Y", "total_amount": 10}
			)
			bill.flags.ignore_links = True
			bill.insert(ignore_permissions=True)
