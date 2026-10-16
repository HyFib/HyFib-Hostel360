"""Two confirmations of the same payment on separate database connections.

In production the checkout callback, ``payment.captured`` and ``order.paid`` routinely arrive
together. Each request reads the transaction before it takes the row lock, so under MariaDB's
snapshot isolation the slower request's locking read fails with "record has changed since last
read" unless it starts a fresh transaction. These tests reproduce that on two real connections,
so the records they use are committed and removed again afterwards.
"""

import frappe

from hostel360.hostel_fees.constants import (
	ALLOCATION,
	INVOICE,
	INVOICE_ITEM,
	PAYMENT,
	TRANSACTION,
	TRANSACTION_INVOICE,
)
from hostel360.hostel_fees.gateways.razorpay import RazorpayGateway
from hostel360.hostel_fees.tests.fixtures import (
	FeeTestCase,
	make_hostel,
	make_invoice,
	make_student,
	payment_entity,
	unique,
)
from hostel360.hostel_fees.transactions import complete_transaction, mark_pending


class TestPaymentConfirmationRace(FeeTestCase):
	def setUp(self):
		super().setUp()
		self.hostel = make_hostel()
		self.student = make_student(hostel=self.hostel)
		self.invoice = make_invoice(self.student, 1500).name
		tx = frappe.get_doc(
			{
				"doctype": TRANSACTION,
				"student": self.student,
				"amount": 1500,
				"currency": "INR",
				"gateway": "Razorpay",
				"gateway_order_id": unique("order_"),
				"status": "Initiated",
				"invoices": [{"invoice": self.invoice, "amount": 1500}],
			}
		)
		tx.flags.ignore_permissions = True
		tx.insert()
		self.transaction = tx.name
		self.payment = RazorpayGateway.normalise_payment(payment_entity(tx.gateway_order_id, 150000))
		# The second connection only sees committed rows.
		frappe.db.commit()  # nosemgrep
		self.addCleanup(self.remove_records)

	def remove_records(self):
		# Frappe's secondary_connection() leaves the second connection active; go back to ours.
		frappe.local.db = self._primary_connection
		frappe.db.rollback()
		receipts = frappe.get_all(PAYMENT, filters={"payment_transaction": self.transaction}, pluck="name")
		for name in receipts:
			frappe.db.delete(ALLOCATION, {"parent": name})
			frappe.db.delete(PAYMENT, {"name": name})
		frappe.db.delete(TRANSACTION_INVOICE, {"parent": self.transaction})
		frappe.db.delete(TRANSACTION, {"name": self.transaction})
		frappe.db.delete(INVOICE_ITEM, {"parent": self.invoice})
		frappe.db.delete(INVOICE, {"name": self.invoice})
		frappe.db.delete("Hostel Student", {"name": self.student})
		frappe.db.delete("Hostel", {"name": self.hostel})
		for doctype, names in (
			(PAYMENT, receipts),
			(TRANSACTION, [self.transaction]),
			(INVOICE, [self.invoice]),
			("Hostel Student", [self.student]),
			("Hostel", [self.hostel]),
		):
			if names:
				frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ("in", names)})
		frappe.db.commit()  # nosemgrep

	def complete_elsewhere(self):
		"""Another request confirms the payment on its own connection and commits."""
		with self.secondary_connection():
			outcome = complete_transaction(self.transaction, self.payment, "webhook")
			frappe.db.commit()  # nosemgrep
		return outcome

	def test_second_confirmation_returns_the_existing_receipt(self):
		# This request has already read the transaction, as every confirmation path does ...
		self.assertEqual(frappe.db.get_value(TRANSACTION, self.transaction, "status"), "Initiated")
		first = self.complete_elsewhere()
		self.assertEqual(first.status, "Success")

		# ... so its locking read now meets a row changed after its snapshot.
		with self.primary_connection():
			second = complete_transaction(self.transaction, self.payment, "checkout")
			self.assertEqual((second.status, second.payment), ("Success", first.payment))
			self.assertEqual(
				frappe.get_all(PAYMENT, filters={"payment_transaction": self.transaction, "docstatus": 1}),
				[frappe._dict(name=first.payment)],
			)
			self.assertEqual(frappe.db.get_value(INVOICE, self.invoice, "status"), "Paid")

	def test_late_pending_notice_does_not_fail_after_completion(self):
		self.assertEqual(frappe.db.get_value(TRANSACTION, self.transaction, "status"), "Initiated")
		self.complete_elsewhere()
		with self.primary_connection():
			self.assertEqual(mark_pending(self.transaction, self.payment), "Success")
