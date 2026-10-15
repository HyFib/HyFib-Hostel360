# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import re

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

	def test_print_formats_fall_back_to_a_tamil_font(self):
		# Student and item names may be in Tamil; without a Tamil face in the stack wkhtmltopdf
		# drops those glyphs. The Latin face comes first so English text and figures keep it.
		for print_format in ("Mess Bill", "Mess Bill Run Summary"):
			css = frappe.db.get_value("Print Format", print_format, "css") or ""
			match = re.search(r"\.h360-mess-bill\s*\{[^}]*font-family:\s*([^;]+);", css)
			self.assertTrue(match, print_format)
			families = [family.strip().strip('"') for family in match.group(1).split(",")]
			self.assertLess(families.index("Inter"), families.index("Noto Sans Tamil"), print_format)
			self.assertIn("Lohit Tamil", families, print_format)
			self.assertEqual(families[-1], "sans-serif", print_format)
