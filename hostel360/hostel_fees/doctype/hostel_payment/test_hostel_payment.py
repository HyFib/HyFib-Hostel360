# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, nowdate

from hostel360.hostel_fees.constants import INVOICE, PAYMENT
from hostel360.hostel_fees.desk import get_outstanding_invoices, record_payment
from hostel360.hostel_fees.tests import fixtures as fee_fixtures
from hostel360.hostel_fees.tests.fixtures import (
	FeeTestCase,
	make_hostel,
	make_invoice,
	make_payment,
	make_student,
)

# Read by the Frappe test runner: fee tests build every linked record they need themselves.
IGNORE_TEST_RECORD_DEPENDENCIES = fee_fixtures.IGNORE_TEST_RECORD_DEPENDENCIES


class TestHostelPayment(FeeTestCase):
	def setUp(self):
		super().setUp()
		self.student = make_student(hostel=make_hostel())
		self.invoice = make_invoice(self.student, 1000)

	def test_allocations_are_required(self):
		with self.assertRaises(frappe.ValidationError):
			make_payment(self.student, [], amount=500)

	def test_amount_must_equal_total_allocated(self):
		with self.assertRaises(frappe.ValidationError):
			make_payment(self.student, [(self.invoice.name, 400)], amount=500)
		with self.assertRaises(frappe.ValidationError):
			make_payment(self.student, [(self.invoice.name, 400)], amount=300)

		payment = make_payment(self.student, [(self.invoice.name, 400)])
		self.assertEqual((payment.amount, payment.total_allocated, payment.unallocated_amount), (400, 400, 0))

	def test_allocation_cannot_exceed_outstanding(self):
		with self.assertRaises(frappe.ValidationError):
			make_payment(self.student, [(self.invoice.name, 1000.01)])

	def test_invoice_must_belong_to_student_and_be_submitted(self):
		other = make_student()
		other_invoice = make_invoice(other, 300)
		with self.assertRaises(frappe.ValidationError):
			make_payment(self.student, [(other_invoice.name, 300)])

		draft = make_invoice(self.student, 300, submit=False)
		with self.assertRaises(frappe.ValidationError):
			make_payment(self.student, [(draft.name, 300)])

	def test_duplicate_invoice_rows_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_payment(self.student, [(self.invoice.name, 300), (self.invoice.name, 200)])

	def test_reference_number_rules(self):
		make_payment(self.student, [(self.invoice.name, 100)], mode="Cash")
		for mode in ("UPI", "Demand Draft", "Cheque", "Bank Transfer"):
			with self.assertRaises(frappe.ValidationError, msg=mode):
				make_payment(self.student, [(self.invoice.name, 100)], mode=mode)
			make_payment(self.student, [(self.invoice.name, 100)], mode=mode, reference_no=f"REF-{mode}")

	def test_online_mode_needs_a_gateway_transaction(self):
		with self.assertRaises(frappe.ValidationError):
			make_payment(self.student, [(self.invoice.name, 100)], mode="Online", reference_no="pay_123")

	def test_future_payment_date_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_payment(self.student, [(self.invoice.name, 100)], payment_date=add_days(nowdate(), 1))

	def test_submit_updates_invoices_and_cancel_reverses(self):
		second = make_invoice(self.student, 500)
		payment = make_payment(self.student, [(self.invoice.name, 1000), (second.name, 200)])
		payment.submit()

		self.assertEqual(
			frappe.db.get_value(INVOICE, self.invoice.name, ["status", "outstanding_amount"]), ("Paid", 0)
		)
		self.assertEqual(
			frappe.db.get_value(INVOICE, second.name, ["status", "outstanding_amount"]), ("Partly Paid", 300)
		)
		self.assertEqual(payment.allocations[0].outstanding_before, 1000)
		self.assertEqual(payment.received_by, "Administrator")

		payment.cancel()
		self.assertEqual(
			frappe.db.get_value(INVOICE, self.invoice.name, ["status", "outstanding_amount"]),
			("Unpaid", 1000),
		)
		self.assertEqual(
			frappe.db.get_value(INVOICE, second.name, ["status", "outstanding_amount"]), ("Unpaid", 500)
		)

	def test_submit_rechecks_outstanding(self):
		# Two drafts for the same dues, as when two counters serve the same student.
		first = make_payment(self.student, [(self.invoice.name, 1000)])
		second = make_payment(self.student, [(self.invoice.name, 1000)])
		first.submit()
		with self.assertRaises(frappe.ValidationError):
			second.submit()
		self.assertEqual(frappe.db.get_value(INVOICE, self.invoice.name, "paid_amount"), 1000)

	def test_payment_received_notification(self):
		payment = make_payment(self.student, [(self.invoice.name, 250)])
		payment.submit()
		sent = self.notified("payment_received", payment.name)
		self.assertEqual(len(sent), 1)
		self.assertEqual(sent[0]["context"]["receipt_number"], payment.name)
		self.assertEqual(sent[0]["context"]["amount"], 250)

	def test_migration_accepts_historic_receipts_quietly(self):
		frappe.flags.hostel360_migration = True
		payment = make_payment(
			self.student, [(self.invoice.name, 1000)], mode="Demand Draft", payment_date="2024-07-10"
		)
		payment.submit()
		frappe.flags.hostel360_migration = False

		self.assertEqual(frappe.db.get_value(INVOICE, self.invoice.name, "status"), "Paid")
		self.assertFalse(self.notified("payment_received", payment.name))

	def test_counter_helpers(self):
		second = make_invoice(self.student, 500, due_date=add_days(nowdate(), 30))
		dues = get_outstanding_invoices(self.student)
		self.assertEqual([row.name for row in dues], [self.invoice.name, second.name])

		result = record_payment(
			self.student,
			"UPI",
			[
				{"invoice": self.invoice.name, "allocated_amount": 1000},
				{"invoice": second.name, "allocated_amount": 0},
			],
			reference_no="UTR998877",
		)
		receipt = frappe.get_doc(PAYMENT, result["payment"])
		self.assertEqual((receipt.docstatus, receipt.amount, len(receipt.allocations)), (1, 1000, 1))
		self.assertEqual(get_outstanding_invoices(self.student)[0].name, second.name)

		with self.assertRaises(frappe.ValidationError):
			record_payment(self.student, "Online", [{"invoice": second.name, "allocated_amount": 500}])

	def test_test_flag_cannot_be_set_on_a_counter_receipt(self):
		# Read-only fields are still writable through the REST API; a counter receipt marked as a
		# test would vanish from the day-end cash summary.
		payment = make_payment(self.student, [(self.invoice.name, 1000)], is_test=1)
		self.assertEqual(payment.is_test, 0)
		payment.submit()
		self.assertTrue(self.notified("payment_received", payment.name))

	def test_cancellation_is_time_stamped(self):
		payment = make_payment(self.student, [(self.invoice.name, 1000)])
		payment.submit()
		self.assertFalse(payment.cancelled_on)
		payment.cancel()
		stored = frappe.db.get_value(PAYMENT, payment.name, ["cancelled_on", "cancelled_by"], as_dict=True)
		self.assertTrue(stored.cancelled_on)
		self.assertEqual(stored.cancelled_by, frappe.session.user)
