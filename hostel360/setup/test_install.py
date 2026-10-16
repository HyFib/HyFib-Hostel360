# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import fmt_money

from hostel360.setup.install import REGIONAL_SETTINGS, sync_system_defaults


class TestRegionalDefaults(IntegrationTestCase):
	def test_desk_formats_amounts_in_rupees(self):
		# The desk formats Currency fields with the global defaults, not System Settings: a site
		# whose defaults kept the setup wizard's values showed "$" and US digit grouping.
		for fieldname in REGIONAL_SETTINGS:
			frappe.db.set_single_value("System Settings", fieldname, REGIONAL_SETTINGS[fieldname])
		frappe.db.set_default("currency", "USD")
		frappe.db.set_default("number_format", "#,###.##")
		frappe.db.set_default("date_format", "mm-dd-yyyy")

		sync_system_defaults()

		self.assertEqual(frappe.db.get_default("currency"), "INR")
		self.assertEqual(frappe.db.get_default("number_format"), "#,##,###.##")
		self.assertEqual(frappe.db.get_default("date_format"), "dd-mm-yyyy")
		self.assertTrue(frappe.db.get_value("Currency", "INR", "enabled"))
		self.assertEqual(fmt_money(150000, currency=frappe.db.get_default("currency")), "₹ 1,50,000.00")
