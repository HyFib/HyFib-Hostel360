# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from hostel360.hostel_fees.constants import INVOICE, PAYMENT, TRANSACTION
from hostel360.hostel_fees.gateways import GatewayError, GatewayNotConfiguredError, get_gateway
from hostel360.hostel_fees.reconciliation import daily_reconciliation, reconcile_pending_transactions
from hostel360.hostel_fees.services import confirm_checkout, create_payment_order
from hostel360.hostel_fees.tests import fixtures as fee_fixtures
from hostel360.hostel_fees.tests.fixtures import (
	TEST_KEY_ID,
	TEST_KEY_SECRET,
	FakeRazorpay,
	FakeResponse,
	FeeTestCase,
	checkout_signature,
	configure_gateway,
	make_hostel,
	make_invoice,
	make_payment,
	make_student,
	payment_entity,
	webhook_body,
	webhook_signature,
)
from hostel360.hostel_fees.transactions import PaymentVerificationError
from hostel360.hostel_fees.webhooks import handle_razorpay_webhook

# Read by the Frappe test runner: fee tests build every linked record they need themselves.
IGNORE_TEST_RECORD_DEPENDENCIES = fee_fixtures.IGNORE_TEST_RECORD_DEPENDENCIES


class TestHostelPaymentTransaction(FeeTestCase):
	def setUp(self):
		super().setUp()
		self.api = FakeRazorpay()
		patcher = patch("hostel360.hostel_fees.gateways.razorpay.requests.request", side_effect=self.api)
		patcher.start()
		self.addCleanup(patcher.stop)

		configure_gateway()
		self.student = make_student(hostel=make_hostel())
		self.invoice = make_invoice(self.student, 1500)

	# -- helpers -----------------------------------------------------------------------------

	def start(self, invoices=None) -> dict:
		return create_payment_order(
			self.student, invoices or [self.invoice.name], "Administrator", "203.0.113.7"
		)

	def checkout_payload(self, result: dict, payment: dict, signature: str | None = None) -> dict:
		order_id = result["checkout"]["order_id"]
		return {
			"razorpay_order_id": order_id,
			"razorpay_payment_id": payment["id"],
			"razorpay_signature": signature or checkout_signature(order_id, payment["id"]),
		}

	def receipts_for(self, transaction: str) -> list[str]:
		return frappe.get_all(
			PAYMENT, filters={"payment_transaction": transaction, "docstatus": 1}, pluck="name"
		)

	def age(self, transaction: str, minutes: int):
		frappe.db.set_value(
			TRANSACTION,
			transaction,
			"creation",
			add_to_date(now_datetime(), minutes=-minutes),
			update_modified=False,
		)

	def calls(self, method: str, suffix: str) -> list:
		return [
			c for c in self.api.calls if c["method"] == method and c["url"].split("?")[0].endswith(suffix)
		]

	# -- order creation ----------------------------------------------------------------------

	def test_order_creation_payload_and_checkout(self):
		result = self.start()

		orders = self.calls("POST", "/v1/orders")
		self.assertEqual(len(orders), 1)
		call = orders[0]
		self.assertEqual(call["url"], "https://api.razorpay.com/v1/orders")
		self.assertEqual(call["auth"], (TEST_KEY_ID, TEST_KEY_SECRET))
		self.assertEqual(call["timeout"], 10)
		self.assertEqual(call["json"]["amount"], 150000)
		self.assertIsInstance(call["json"]["amount"], int)
		self.assertEqual(call["json"]["currency"], "INR")
		self.assertEqual(call["json"]["receipt"], result["transaction"])
		self.assertEqual(call["json"]["notes"]["transaction"], result["transaction"])
		self.assertEqual(call["json"]["notes"]["student"], self.student)

		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual(
			(tx.status, tx.amount, tx.currency, tx.gateway), ("Initiated", 1500, "INR", "Razorpay")
		)
		self.assertEqual(tx.gateway_order_id, result["checkout"]["order_id"])
		self.assertEqual([(row.invoice, row.amount) for row in tx.invoices], [(self.invoice.name, 1500)])
		self.assertEqual((tx.initiated_by, tx.ip_address, tx.test_mode), ("Administrator", "203.0.113.7", 1))

		self.assertEqual(result["gateway"], "razorpay")
		checkout = result["checkout"]
		self.assertEqual(
			(checkout["key"], checkout["amount"], checkout["currency"]), (TEST_KEY_ID, 150000, "INR")
		)
		self.assertEqual(checkout["name"], "Test Hostels")
		self.assertTrue(checkout["prefill"]["contact"].startswith("+91"))
		self.assertIn("@example.com", checkout["prefill"]["email"])
		self.assertEqual(checkout["notes"]["transaction"], tx.name)

	def test_repeat_request_reuses_open_order(self):
		first = self.start()
		second = self.start()
		self.assertEqual(first["transaction"], second["transaction"])
		self.assertEqual(len(self.calls("POST", "/v1/orders")), 1)

	def test_order_rules(self):
		other_invoice = make_invoice(make_student(), 400)
		with self.assertRaises(frappe.PermissionError):
			self.start([other_invoice.name])

		paid = make_invoice(self.student, 200)
		make_payment(self.student, [(paid.name, 200)]).submit()
		with self.assertRaises(frappe.ValidationError):
			self.start([paid.name])

		with self.assertRaises(frappe.ValidationError):
			create_payment_order(self.student, [], "Administrator")

	def test_disabled_gateway_never_called(self):
		configure_gateway(enabled=0)
		with self.assertRaises(GatewayNotConfiguredError):
			self.start()
		self.assertEqual(self.api.calls, [])

	def test_unattempted_overlapping_order_is_replaced(self):
		second_invoice = make_invoice(self.student, 500)
		first = self.start()
		combined = self.start([self.invoice.name, second_invoice.name])

		self.assertNotEqual(first["transaction"], combined["transaction"])
		self.assertEqual(frappe.db.get_value(TRANSACTION, first["transaction"], "status"), "Expired")
		self.assertEqual(frappe.db.get_value(TRANSACTION, combined["transaction"], "amount"), 2000)

	def test_overlapping_order_with_payment_in_flight_blocks(self):
		second_invoice = make_invoice(self.student, 500)
		first = self.start()
		self.api.pay(first["checkout"]["order_id"], status="created")
		with self.assertRaises(frappe.ValidationError):
			self.start([self.invoice.name, second_invoice.name])

	def test_gateway_errors_are_reported_clearly(self):
		self.api.fail_next = FakeResponse(
			401, {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}}
		)
		with self.assertRaises(GatewayError) as error:
			self.start()
		self.assertIn("credentials", str(error.exception))

		self.api.fail_next = requests.Timeout("read timed out")
		with self.assertRaises(GatewayError) as error:
			self.start()
		self.assertIn("did not respond", str(error.exception))
		self.assertFalse(frappe.db.exists(TRANSACTION, {"student": self.student}))

	# -- signatures --------------------------------------------------------------------------

	def test_checkout_signature_verification(self):
		gateway = get_gateway()
		valid = checkout_signature("order_A", "pay_B")
		self.assertTrue(gateway.verify_checkout("order_A", "pay_B", valid))
		self.assertFalse(gateway.verify_checkout("order_A", "pay_C", valid))
		self.assertFalse(
			gateway.verify_checkout(
				"order_A", "pay_B", checkout_signature("order_A", "pay_B", "other-secret")
			)
		)
		self.assertFalse(gateway.verify_checkout("order_A", "pay_B", ""))

	def test_webhook_signature_verification(self):
		gateway = get_gateway()
		body = b'{"event": "payment.captured"}'
		self.assertTrue(gateway.verify_webhook(body, webhook_signature(body)))
		self.assertFalse(gateway.verify_webhook(body + b" ", webhook_signature(body)))
		self.assertFalse(gateway.verify_webhook(body, webhook_signature(body, "other-secret")))
		self.assertFalse(gateway.verify_webhook(body, None))

	# -- checkout confirmation ---------------------------------------------------------------

	def test_confirm_checkout_issues_one_receipt(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"])
		payload = self.checkout_payload(result, payment)

		first = confirm_checkout(result["transaction"], payload)
		self.assertEqual(first["status"], "Success")
		self.assertTrue(first["receipt_number"])
		self.assertEqual(first["payment"], first["receipt_number"])

		second = confirm_checkout(result["transaction"], payload)
		self.assertEqual(second["receipt_number"], first["receipt_number"])
		self.assertEqual(self.receipts_for(result["transaction"]), [first["receipt_number"]])

		receipt = frappe.get_doc(PAYMENT, first["receipt_number"])
		self.assertEqual(
			(receipt.mode_of_payment, receipt.amount, receipt.reference_no), ("Online", 1500, payment["id"])
		)
		self.assertEqual(
			[(row.invoice, row.allocated_amount) for row in receipt.allocations], [(self.invoice.name, 1500)]
		)
		self.assertEqual(
			frappe.db.get_value(INVOICE, self.invoice.name, ["status", "outstanding_amount"]), ("Paid", 0)
		)

		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual(
			(tx.status, tx.payment, tx.gateway_payment_id, tx.payment_method),
			("Success", receipt.name, payment["id"], "upi"),
		)
		self.assertNotIn("payer@example.com", tx.gateway_response or "")
		self.assertTrue(self.notified("payment_received", receipt.name))

	def test_confirm_checkout_rejects_amount_mismatch(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"], amount=100)
		with self.assertRaises(PaymentVerificationError):
			confirm_checkout(result["transaction"], self.checkout_payload(result, payment))

		self.assertEqual(self.receipts_for(result["transaction"]), [])
		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual(tx.status, "Failed")
		self.assertIn("amount mismatch", tx.failure_reason)
		self.assertEqual(frappe.db.get_value(INVOICE, self.invoice.name, "status"), "Unpaid")
		self.assertTrue(self.alerts_in("Payments"))

	def test_confirm_checkout_rejects_bad_signature(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"])
		with self.assertRaises(PaymentVerificationError):
			confirm_checkout(
				result["transaction"], self.checkout_payload(result, payment, signature="0" * 64)
			)

		self.assertEqual(self.calls("GET", f"/payments/{payment['id']}"), [])
		self.assertEqual(self.receipts_for(result["transaction"]), [])
		self.assertTrue(self.alerts_in("Security"))

	def test_confirm_checkout_rejects_another_order(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"])
		payload = {
			"razorpay_order_id": "order_SOMEONEELSE",
			"razorpay_payment_id": payment["id"],
			"razorpay_signature": checkout_signature("order_SOMEONEELSE", payment["id"]),
		}
		with self.assertRaises(PaymentVerificationError):
			confirm_checkout(result["transaction"], payload)
		self.assertEqual(self.receipts_for(result["transaction"]), [])

	def test_confirm_checkout_captures_authorised_payment(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"], status="authorized")
		outcome = confirm_checkout(result["transaction"], self.checkout_payload(result, payment))
		self.assertEqual(outcome["status"], "Success")
		capture = self.calls("POST", f"/payments/{payment['id']}/capture")
		self.assertEqual(len(capture), 1)
		self.assertEqual(capture[0]["json"], {"amount": 150000, "currency": "INR"})

	def test_confirm_checkout_reports_failed_payment(self):
		result = self.start()
		payment = self.api.pay(
			result["checkout"]["order_id"], status="failed", error_description="Card declined"
		)
		outcome = confirm_checkout(result["transaction"], self.checkout_payload(result, payment))
		self.assertEqual((outcome["status"], outcome["receipt_number"]), ("Failed", None))
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Failed")

	# -- webhook -----------------------------------------------------------------------------

	def deliver(self, event: str, payment: dict, signature: str | None = None):
		body = webhook_body(event, payment)
		return handle_razorpay_webhook(body, signature or webhook_signature(body), "evt_test")

	def test_webhook_rejects_bad_signature_and_alerts(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"])
		status, response = self.deliver("payment.captured", payment, signature="f" * 64)

		self.assertEqual((status, response["status"]), (400, "invalid_signature"))
		self.assertEqual(self.receipts_for(result["transaction"]), [])
		security = self.alerts_in("Security")
		self.assertEqual(len(security), 1)
		self.assertEqual(security[0].kwargs["severity"], "Warning")

	def test_webhook_completes_payment_idempotently(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"])

		self.assertEqual(self.deliver("payment.captured", payment)[0], 200)
		self.assertEqual(self.deliver("payment.captured", payment)[0], 200)
		order = {"id": result["checkout"]["order_id"], "entity": "order", "status": "paid"}
		body = webhook_body("order.paid", payment, order)
		self.assertEqual(handle_razorpay_webhook(body, webhook_signature(body))[0], 200)

		self.assertEqual(len(self.receipts_for(result["transaction"])), 1)
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Success")
		self.assertEqual(frappe.session.user, "Administrator")

	def test_webhook_failed_attempt_then_success(self):
		result = self.start()
		order_id = result["checkout"]["order_id"]
		failed = self.api.pay(
			order_id, status="failed", error_description="Payment was cancelled by the user"
		)
		self.deliver("payment.failed", failed)
		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual((tx.status, tx.failure_reason), ("Failed", "Payment was cancelled by the user"))

		captured = self.api.pay(order_id)
		self.deliver("payment.captured", captured)
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Success")

		# A late failure notice never undoes a completed payment.
		self.deliver("payment.failed", failed)
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Success")

	def test_webhook_amount_mismatch_is_refused(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"], amount=1)
		status, response = self.deliver("payment.captured", payment)
		self.assertEqual((status, response["status"]), (200, "rejected"))
		self.assertEqual(self.receipts_for(result["transaction"]), [])

	def test_webhook_for_unknown_order_is_ignored(self):
		status, response = self.deliver("payment.captured", payment_entity("order_NOTOURS", 1000))
		self.assertEqual((status, response["status"]), (200, "ignored"))

	def test_webhook_without_secret_is_refused(self):
		configure_gateway(webhook_secret="")
		body = webhook_body("payment.captured", payment_entity("order_X", 1000))
		status, _response = handle_razorpay_webhook(body, webhook_signature(body))
		self.assertEqual(status, 400)

	# -- money arriving after dues changed ---------------------------------------------------

	def test_payment_exceeding_dues_is_held_unallocated(self):
		result = self.start()
		make_payment(self.student, [(self.invoice.name, 500)]).submit()  # paid at the counter meanwhile

		payment = self.api.pay(result["checkout"]["order_id"])
		self.deliver("payment.captured", payment)

		receipt = frappe.get_doc(PAYMENT, self.receipts_for(result["transaction"])[0])
		self.assertEqual(
			(receipt.amount, receipt.total_allocated, receipt.unallocated_amount), (1500, 1000, 500)
		)
		self.assertEqual(frappe.db.get_value(INVOICE, self.invoice.name, "status"), "Paid")
		self.assertTrue(any(key.startswith("payment-unallocated-") for key in self.alert_keys()))

	def test_invoice_cannot_be_cancelled_during_payment(self):
		self.start()
		with self.assertRaises(frappe.ValidationError):
			self.invoice.reload()
			self.invoice.cancel()

	# -- reconciliation ----------------------------------------------------------------------

	def test_reconciliation_completes_missed_payment(self):
		result = self.start()
		self.api.pay(result["checkout"]["order_id"])
		self.age(result["transaction"], 15)

		reconcile_pending_transactions()
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Success")
		self.assertEqual(len(self.receipts_for(result["transaction"])), 1)

	def test_reconciliation_leaves_recent_transactions_alone(self):
		self.start()
		before = len(self.api.calls)
		reconcile_pending_transactions()
		self.assertEqual(len(self.api.calls), before)

	def test_reconciliation_expires_abandoned_and_fails_declined(self):
		abandoned = self.start()
		self.age(abandoned["transaction"], 120)

		other = make_invoice(self.student, 300)
		declined = self.start([other.name])
		self.api.pay(
			declined["checkout"]["order_id"], status="failed", error_description="Insufficient balance"
		)
		self.age(declined["transaction"], 120)

		reconcile_pending_transactions()
		self.assertEqual(frappe.db.get_value(TRANSACTION, abandoned["transaction"], "status"), "Expired")
		self.assertEqual(
			frappe.db.get_value(TRANSACTION, declined["transaction"], ["status", "failure_reason"]),
			("Failed", "Insufficient balance"),
		)

	def test_reconciliation_skips_when_disabled(self):
		result = self.start()
		self.age(result["transaction"], 120)
		configure_gateway(enabled=0)
		self.api.calls.clear()

		self.assertEqual(reconcile_pending_transactions(), {"skipped": True})
		self.assertEqual(daily_reconciliation()["verified"], 0)
		self.assertEqual(self.api.calls, [])
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Initiated")

	def test_daily_reconciliation(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"])
		confirm_checkout(result["transaction"], self.checkout_payload(result, payment))

		self.alerts.reset_mock()
		outcome = daily_reconciliation()
		self.assertGreaterEqual(outcome["verified"], 1)
		self.assertTrue(frappe.db.get_value(TRANSACTION, result["transaction"], "reconciled_on"))

		receipt = frappe.get_doc(PAYMENT, self.receipts_for(result["transaction"])[0])
		receipt.cancel()
		self.alerts.reset_mock()
		daily_reconciliation()
		daily = [
			c for c in self.alerts.call_args_list if c.args and c.args[0] == "payments-daily-reconciliation"
		]
		self.assertEqual(len(daily), 1)
		issues = daily[0].kwargs["details"]["issues"]
		self.assertTrue(any(issue.get("transaction") == result["transaction"] for issue in issues))

		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		tx.mark_refunded("Refund rfnd_123 processed on request")
		self.assertEqual(frappe.db.get_value(TRANSACTION, tx.name, "status"), "Refunded")
