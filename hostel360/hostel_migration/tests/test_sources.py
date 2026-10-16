"""Reading legacy CSV and Excel exports."""

import datetime
import os
import shutil
import tempfile

from frappe.tests import UnitTestCase

from hostel360.hostel_migration.mapping import MappingError
from hostel360.hostel_migration.sources import missing_columns, read_table, resolve_inside


class TestSources(UnitTestCase):
	def setUp(self):
		self.folder = tempfile.mkdtemp(prefix="h360-src-test-")
		self.addCleanup(shutil.rmtree, self.folder, True)

	def write(self, name: str, data: bytes) -> str:
		with open(os.path.join(self.folder, name), "wb") as f:
			f.write(data)
		return name

	def test_utf8_bom_and_blank_rows(self):
		self.write("s.csv", "﻿id,name\r\n1,அருண்\r\n,\r\n2,Kavya\r\n".encode())
		table = read_table(self.folder, "s.csv")
		self.assertEqual(table.headers, ["id", "name"])
		self.assertEqual([(line, row["name"]) for line, row in table.rows], [(2, "அருண்"), (4, "Kavya")])
		self.assertEqual(len(table.checksum), 64)

	def test_windows_1252_and_semicolons(self):
		self.write("s.csv", "id;name\n1;Caf\xe9 Supplies\n".encode("cp1252"))
		table = read_table(self.folder, "s.csv")
		self.assertEqual(table.encoding, "cp1252")
		self.assertEqual(table.rows[0][1]["name"], "Café Supplies")

	def test_quoted_multiline_cells_keep_line_numbers(self):
		self.write("s.csv", b'id,address\n1,"12, Main Road\nKaraikudi"\n2,Madurai\n')
		table = read_table(self.folder, "s.csv")
		self.assertEqual(table.rows[0][1]["address"], "12, Main Road\nKaraikudi")
		self.assertEqual(table.rows[1][0], 4)

	def test_header_problems(self):
		self.write("dup.csv", b"id,Name,name\n1,a,b\n")
		with self.assertRaises(MappingError):
			read_table(self.folder, "dup.csv")
		self.write("blank.csv", b"id,,name\n1,a,b\n")
		with self.assertRaises(MappingError):
			read_table(self.folder, "blank.csv")
		self.write("empty.csv", b"")
		with self.assertRaises(MappingError):
			read_table(self.folder, "empty.csv")

	def test_extra_values_are_refused(self):
		self.write("s.csv", b"id,name\n1,a,unexpected\n")
		with self.assertRaises(MappingError):
			read_table(self.folder, "s.csv")

	def test_missing_file_and_traversal(self):
		with self.assertRaises(MappingError):
			read_table(self.folder, "nothing.csv")
		with self.assertRaises(MappingError):
			resolve_inside(self.folder, "../outside.csv")

	def test_xlsx(self):
		from openpyxl import Workbook

		workbook = Workbook()
		sheet = workbook.active
		sheet.title = "Students"
		sheet.append([])
		sheet.append(["ID", "DOB", "Fee"])
		sheet.append(["S1", datetime.datetime(2004, 3, 14), 12500])
		sheet.append([None, None, None])
		sheet.append(["S2", "15-06-2005", 9800.5])
		path = os.path.join(self.folder, "s.xlsx")
		workbook.save(path)

		table = read_table(self.folder, "s.xlsx", sheet="Students")
		self.assertEqual(table.headers, ["ID", "DOB", "Fee"])
		self.assertEqual([line for line, _row in table.rows], [3, 5])
		self.assertEqual(table.rows[0][1]["DOB"], datetime.datetime(2004, 3, 14))
		with self.assertRaises(MappingError):
			read_table(self.folder, "s.xlsx", sheet="Fees")

	def test_missing_columns_ignore_case(self):
		self.write("s.csv", b"ID,Name\n1,a\n")
		table = read_table(self.folder, "s.csv")
		self.assertEqual(missing_columns(table, {"id", "name", "mobile"}), ["mobile"])
