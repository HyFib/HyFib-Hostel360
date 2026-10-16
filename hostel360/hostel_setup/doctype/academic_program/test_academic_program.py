# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


def make_department():
	name = f"Test Department {frappe.generate_hash(length=6)}"
	return frappe.get_doc({"doctype": "Academic Department", "department_name": name}).insert()


class TestAcademicProgram(IntegrationTestCase):
	def test_duration_bounds(self):
		department = make_department()
		program = frappe.get_doc(
			{
				"doctype": "Academic Program",
				"program_name": f"B.E. Test {frappe.generate_hash(length=6)}",
				"department": department.name,
				"degree_level": "UG",
				"duration_years": 9,
			}
		)
		self.assertRaises(frappe.ValidationError, program.insert)
		program.duration_years = 4
		program.insert()
		self.assertEqual(program.department, department.name)
