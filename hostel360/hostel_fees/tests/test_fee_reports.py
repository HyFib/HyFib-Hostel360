"""Fee reports and printable documents."""

import frappe
from frappe.utils import add_days, nowdate

from hostel360.hostel_fees.constants import PAYMENT
from hostel360.hostel_fees.report.daily_collection_summary.daily_collection_summary import (
	execute as daily_collection_summary,
)
from hostel360.hostel_fees.report.fee_collection.fee_collection import apportion
from hostel360.hostel_fees.report.fee_collection.fee_collection import execute as fee_collection
from hostel360.hostel_fees.report.fee_dues.fee_dues import bucket_for
from hostel360.hostel_fees.report.fee_dues.fee_dues import execute as fee_dues
from hostel360.hostel_fees.report.gateway_reconciliation.gateway_reconciliation import (
	execute as gateway_reconciliation,
)
from hostel360.hostel_fees.services import get_fee_structure_pdf, get_receipt_pdf
from hostel360.hostel_fees.tests.fixtures import (
	FeeTestCase,
	make_academic_year,
	make_hostel,
	make_invoice,
	make_payment,
	make_student,
	unique,
)


class TestFeeReports(FeeTestCase):
	def setUp(self):
		super().setUp()
		self.hostel = make_hostel()
		self.student = make_student(hostel=self.hostel)

	def test_fee_collection_splits_by_fee_head(self):
		invoice = make_invoice(
			self.student,
			items=[
				{"fee_head": "Hostel Rent", "amount": 3000},
				{"fee_head": "Electricity Charges", "amount": 1000},
			],
		)
		receipt = make_payment(self.student, [(invoice.name, 2000)], mode="UPI", reference_no="UTR1")
		receipt.submit()

		columns, data, _message, chart, summary = fee_collection(
			{"from_date": nowdate(), "to_date": nowdate(), "hostel": self.hostel}
		)
		row = next(r for r in data if r["payment"] == receipt.name)
		self.assertEqual(row["amount"], 2000)
		self.assertEqual(row["hostel_rent"], 1500)
		self.assertEqual(row["electricity_charges"], 500)
		self.assertIn("hostel_rent", [c["fieldname"] for c in columns])
		self.assertEqual(chart["type"], "bar")
		self.assertEqual(summary[0]["value"], 2000)

	def test_apportion_keeps_totals_exact(self):
		items = [frappe._dict(fee_head=h, amount=a) for h, a in (("A", 1), ("B", 1), ("C", 1))]
		shares = apportion(100, items)
		self.assertEqual(sum(amount for _head, amount in shares), 100)
		self.assertEqual([amount for _head, amount in shares], [33.33, 33.33, 33.34])

	def test_fee_dues_ageing(self):
		self.assertEqual(
			[bucket_for(d) for d in (-1, 0, 30, 31, 60, 61, 90, 91)],
			[
				"not_due",
				"range_0_30",
				"range_0_30",
				"range_31_60",
				"range_31_60",
				"range_61_90",
				"range_61_90",
				"range_90_plus",
			],
		)
		old = make_invoice(
			self.student, 700, posting_date=add_days(nowdate(), -100), due_date=add_days(nowdate(), -95)
		)
		recent = make_invoice(
			self.student, 300, posting_date=add_days(nowdate(), -20), due_date=add_days(nowdate(), -10)
		)
		future = make_invoice(self.student, 200)

		_columns, data, _message, _chart, summary = fee_dues({"as_on_date": nowdate(), "hostel": self.hostel})
		by_invoice = {row.invoice: row for row in data}
		self.assertEqual(by_invoice[old.name].range_90_plus, 700)
		self.assertEqual(by_invoice[old.name].days_past_due, 95)
		self.assertEqual(by_invoice[recent.name].range_0_30, 300)
		self.assertEqual(by_invoice[future.name].not_due, 200)
		self.assertEqual(summary[0]["value"], 1200)
		self.assertEqual(summary[1]["value"], 1000)

		_columns, grouped, *_rest = fee_dues(
			{"as_on_date": nowdate(), "hostel": self.hostel, "group_by": "Student"}
		)
		self.assertEqual(len(grouped), 1)
		self.assertEqual((grouped[0].invoices, grouped[0].outstanding_amount), (3, 1200))

	def test_daily_collection_summary(self):
		invoice = make_invoice(self.student, 1000)
		cash = make_payment(self.student, [(invoice.name, 600)])
		cash.submit()
		cancelled = make_payment(self.student, [(invoice.name, 100)])
		cancelled.submit()
		cancelled.cancel()

		_columns, data, _message, _chart, summary = daily_collection_summary(
			{"date": nowdate(), "hostel": self.hostel}
		)
		self.assertEqual(len(data), 1)
		self.assertEqual((data[0].mode_of_payment, data[0].receipts, data[0].amount), ("Cash", 1, 600))
		self.assertEqual((data[0].cancelled_receipts, data[0].cancelled_amount), (1, 100))
		self.assertEqual(summary[1]["value"], 600)

	def test_gateway_reconciliation_runs(self):
		columns, data, *_rest = gateway_reconciliation(
			{"from_date": add_days(nowdate(), -7), "to_date": nowdate(), "only_issues": 1}
		)
		self.assertIn("issue", [c["fieldname"] for c in columns])
		self.assertTrue(all(row.issue for row in data))

	def assertPdf(self, render):
		"""PDF bytes from ``render``; tolerate only wkhtmltopdf failing to resolve the site's own host.

		Development containers cannot resolve <site>.localhost for the stylesheets wkhtmltopdf fetches;
		the template itself is still rendered and checked.
		"""
		try:
			pdf = render()
		except OSError as e:
			if "HostNotFoundError" not in str(e):
				raise
			return
		self.assertTrue(pdf.startswith(b"%PDF"))

	def test_receipt_and_fee_structure_documents(self):
		invoice = make_invoice(self.student, 1000)
		receipt = make_payment(self.student, [(invoice.name, 1000)])
		receipt.submit()
		self.assertPdf(lambda: get_receipt_pdf(receipt.name))

		html = frappe.get_print(PAYMENT, receipt.name, print_format="Hostel Fee Receipt")
		self.assertIn("One Thousand", html)
		self.assertIn(receipt.name, html)
		self.assertIn(invoice.name, html)

		invoice_html = frappe.get_print("Hostel Fee Invoice", invoice.name, print_format="Hostel Fee Invoice")
		self.assertIn(receipt.name, invoice_html)
		self.assertIn("Paid in full", invoice_html)

		draft = make_payment(self.student, [(make_invoice(self.student, 100).name, 100)])
		with self.assertRaises(frappe.DoesNotExistError):
			get_receipt_pdf(draft.name)

		with self.assertRaises(frappe.DoesNotExistError):
			get_fee_structure_pdf(make_student(hostel=make_hostel()))

		structure = frappe.get_doc(
			{
				"doctype": "Hostel Fee Structure",
				"structure_name": unique("PDF Structure "),
				"academic_year": make_academic_year(),
				"hostel": self.hostel,
				"due_date": add_days(nowdate(), 10),
				"items": [
					{"fee_head": "Hostel Rent", "amount": 1000},
					{"fee_head": "Caution Deposit", "amount": 500},
				],
			}
		).insert()
		self.assertPdf(lambda: get_fee_structure_pdf(self.student))
		structure_html = frappe.get_print(
			"Hostel Fee Structure", structure.name, print_format="Hostel Fee Structure"
		)
		self.assertIn("refundable", structure_html)
		self.assertIn("One Thousand, Five Hundred", structure_html)
