"""Cell transforms used by migration mappings."""

import datetime
from decimal import Decimal

from frappe.tests import UnitTestCase

from hostel360.hostel_migration.transforms import (
	TransformError,
	apply_transforms,
	apply_value_map,
	clean_null,
	normalise_transform,
	parse_decimal,
	to_text,
)


def run(value, *transforms, context=None):
	return apply_transforms(value, list(transforms), context or {})


class TestTextTransforms(UnitTestCase):
	def test_strip_and_spaces(self):
		self.assertEqual(run("  Arun  ", "strip"), "Arun")
		self.assertEqual(run("  Arun   Kumar ", "collapse_spaces"), "Arun Kumar")
		self.assertEqual(run("  bh1 ", "upper"), "BH1")
		self.assertEqual(run(" ARUN@EXAMPLE.COM ", "lower"), "arun@example.com")

	def test_title_keeps_initials_and_mixed_case(self):
		self.assertEqual(run("ARUN KUMAR", "title"), "Arun Kumar")
		self.assertEqual(run("k.arun", "title"), "K.Arun")
		self.assertEqual(run("selvi-rani", "title"), "Selvi-Rani")
		self.assertEqual(run("McDonald o'NEIL", "title"), "McDonald O'Neil")

	def test_split_name(self):
		self.assertEqual(run("Arun Kumar R", {"name": "split_name", "part": "first"}), "Arun")
		self.assertEqual(run("Arun Kumar R", {"name": "split_name", "part": "last"}), "Kumar R")
		self.assertIsNone(run("Arun", {"name": "split_name", "part": "last"}))
		with self.assertRaises(TransformError):
			run("Arun", {"name": "split_name", "part": "middle"})

	def test_replace_prefix_suffix_digits_truncate(self):
		self.assertEqual(run("A B", {"name": "replace", "old": " ", "new": ""}), "AB")
		self.assertEqual(run("101", {"prefix": "BH1-"}), "BH1-101")
		self.assertEqual(run("101", {"name": "suffix", "value": "-A"}), "101-A")
		self.assertEqual(run("UTR: 12-34 56", "digits"), "123456")
		self.assertEqual(run("abcdef", {"name": "truncate", "length": 3}), "abc")

	def test_empty_values_short_circuit(self):
		self.assertIsNone(run("   ", "strip", "upper"))
		self.assertIsNone(run(None, "date"))
		self.assertIsNone(clean_null("NULL"))
		self.assertIsNone(clean_null(" n/a "))
		self.assertIsNone(clean_null("-"))
		self.assertEqual(clean_null("0"), "0")
		self.assertEqual(clean_null("NULL", null_values=("NIL",)), "NULL")

	def test_unknown_transform_is_rejected(self):
		with self.assertRaises(ValueError):
			normalise_transform("shout")
		with self.assertRaises(ValueError):
			normalise_transform({"formats": ["%d"]})


class TestDateTransforms(UnitTestCase):
	def test_common_indian_formats(self):
		for raw in ("15-06-2024", "15/06/2024", "2024-06-15", "15.06.2024", "15-Jun-2024", "15 Jun 2024"):
			self.assertEqual(run(raw, "date"), "2024-06-15", raw)

	def test_explicit_formats_and_context(self):
		self.assertEqual(run("06/15/2024", {"name": "date", "formats": ["%m/%d/%Y"]}), "2024-06-15")
		with self.assertRaises(TransformError):
			run("06/15/2024", "date", context={"date_formats": ["%d/%m/%Y"]})

	def test_spreadsheet_values(self):
		self.assertEqual(run(datetime.datetime(2024, 6, 15, 10, 30), "date"), "2024-06-15")
		self.assertEqual(run(datetime.date(2024, 6, 15), "date"), "2024-06-15")
		self.assertEqual(run(45458, {"name": "date", "excel_serial": True}), "2024-06-15")
		self.assertEqual(run("45458", {"name": "date", "excel_serial": True}), "2024-06-15")

	def test_bad_dates_explain_themselves(self):
		with self.assertRaises(TransformError) as ctx:
			run("31-02-2024", "date")
		self.assertIn("31-02-2024", str(ctx.exception))
		with self.assertRaises(TransformError):
			run("01-01-1850", "date")

	def test_datetime(self):
		self.assertEqual(run("10-01-2026 16:20", "datetime"), "2026-01-10 16:20:00")
		self.assertEqual(run("10/01/2026 04:20 PM", "datetime"), "2026-01-10 16:20:00")
		self.assertEqual(run("2026-01-10", "datetime"), "2026-01-10 00:00:00")
		self.assertEqual(run(datetime.datetime(2026, 1, 10, 16, 20, 5, 999), "datetime"), "2026-01-10 16:20:05")
		with self.assertRaises(TransformError):
			run("tomorrow", "datetime")


class TestNumberTransforms(UnitTestCase):
	def test_indian_amounts(self):
		self.assertEqual(run("Rs. 12,500/-", "decimal"), 12500.0)
		self.assertEqual(run("₹ 1,00,000.50", "decimal"), 100000.5)
		self.assertEqual(run("INR 450", "decimal"), 450.0)
		self.assertEqual(run("(450.00)", "decimal"), -450.0)
		self.assertEqual(run("450 Dr", "decimal"), -450.0)
		self.assertEqual(run("12.345", "decimal"), 12.35)
		self.assertEqual(run(1200, "decimal"), 1200.0)
		self.assertEqual(parse_decimal("19,000"), Decimal("19000"))

	def test_amount_errors(self):
		with self.assertRaises(TransformError):
			run("twelve", "decimal")
		with self.assertRaises(TransformError):
			run("-5", {"name": "decimal", "non_negative": True})
		with self.assertRaises(TransformError):
			run("NaN", "decimal")

	def test_integer(self):
		self.assertEqual(run("4", "integer"), 4)
		self.assertEqual(run(4.0, "integer"), 4)
		self.assertEqual(run("0", "integer"), 0)
		with self.assertRaises(TransformError):
			run("4.5", "integer")

	def test_boolean(self):
		for truthy in ("Y", "yes", "TRUE", "1", "Active", 1, True):
			self.assertEqual(run(truthy, "boolean"), 1, truthy)
		for falsy in ("N", "no", "False", "0", "inactive", 0, False):
			self.assertEqual(run(falsy, "boolean"), 0, falsy)
		self.assertEqual(run("Staying", {"name": "boolean", "true": ["staying"]}), 1)
		with self.assertRaises(TransformError):
			run("maybe", "boolean")

	def test_to_text_renders_spreadsheet_numbers_plainly(self):
		self.assertEqual(to_text(2021.0), "2021")
		self.assertEqual(to_text(Decimal("10.50")), "10.50")
		self.assertEqual(to_text(datetime.date(2024, 1, 2)), "2024-01-02")


class TestContactTransforms(UnitTestCase):
	def test_mobile_numbers(self):
		for raw in ("+91 98765 43210", "09876543210", "91-9876543210", "98765-43210", 9876543210):
			self.assertEqual(run(raw, "mobile"), "9876543210", raw)

	def test_bad_mobile_does_not_pile_up_messages(self):
		import frappe

		before = len(frappe.local.message_log)
		with self.assertRaises(TransformError) as ctx:
			run("12345", "mobile")
		self.assertIn("12345", str(ctx.exception))
		self.assertEqual(len(frappe.local.message_log), before)

	def test_email_and_pincode(self):
		self.assertEqual(run(" Arun.K@Example.COM ", "email"), "arun.k@example.com")
		with self.assertRaises(TransformError):
			run("arun@", "email")
		self.assertEqual(run("630 003", "pincode"), "630003")
		with self.assertRaises(TransformError):
			run("063000", "pincode")


class TestValueMap(UnitTestCase):
	def test_case_insensitive_lookup(self):
		value_map = {"BOYS": "Male", "Girls": "Female"}
		self.assertEqual(apply_value_map("boys", value_map), "Male")
		self.assertEqual(apply_value_map(" GIRLS ", value_map), "Female")

	def test_strict_and_passthrough(self):
		value_map = {"M": "Male"}
		self.assertEqual(apply_value_map("Male", value_map), "Male")
		with self.assertRaises(TransformError):
			apply_value_map("X", value_map, strict=True)
		self.assertIsNone(apply_value_map(None, value_map, strict=True))

	def test_map_to_empty(self):
		self.assertEqual(apply_value_map("CURRENT", {"CURRENT": ""}, strict=True), "")
