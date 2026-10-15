# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestAcademicDepartment(IntegrationTestCase):
	def test_code_is_upper_cased(self):
		name = f"Test Department {frappe.generate_hash(length=6)}"
		doc = frappe.get_doc(
			{
				"doctype": "Academic Department",
				"department_name": f"  {name} ",
				"department_code": f"td{frappe.generate_hash(length=4)}",
			}
		).insert()
		self.assertEqual(doc.name, name)
		self.assertEqual(doc.department_code, doc.department_code.upper())
