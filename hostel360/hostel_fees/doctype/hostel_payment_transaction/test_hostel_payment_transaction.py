# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
import requests
from frappe.utils import add_to_date, now_datetime, nowdate

from hostel360.hostel_fees.constants import INVOICE, PAYMENT, TRANSACTION
from hostel360.hostel_fees.gateways import GatewayError, GatewayNotConfiguredError, get_gateway
from hostel360.hostel_fees.reconciliation import daily_reconciliation, reconcile_pending_transactions
from hostel360.hostel_fees.report.fee_collection.fee_collection import execute as fee_collection
from hostel360.hostel_fees.report.gateway_reconciliation.gateway_reconciliation import (
	execute as gateway_reconciliation,
)
from hostel360.hostel_fees.services import (
	confirm_checkout,
	create_payment_order,
	get_fee_summary,
	online_payment_available,
)
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
from hostel360.hostel_fees.webhooks import (
	MAX_UNVERIFIED_BODY_BYTES,
	NOT_CONFIGURED_ALERT_KEY,
	handle_razorpay_webhook,
)

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
		configure_gateway(live=True)
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
		self.assertEqual(receipt.is_test, 0)
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
		# Razorpay lets the payer retry on the same order, so the transaction stays open.
		self.assertEqual(
			frappe.db.get_value(TRANSACTION, result["transaction"], ["status", "failure_reason"]),
			("Pending", "Card declined"),
		)

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
		self.assertEqual(security[0].kwargs["details"]["transaction"], result["transaction"])

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
		# The order can still be paid: the transaction stays open and a new request reuses it
		# instead of creating a second order, while the portal still lets the payer try again.
		self.assertEqual((tx.status, tx.failure_reason), ("Pending", "Payment was cancelled by the user"))
		invoice = next(i for i in get_fee_summary(self.student)["invoices"] if i.name == self.invoice.name)
		self.assertFalse(invoice.payment_in_progress)
		self.assertEqual(self.start()["transaction"], result["transaction"])
		self.assertEqual(len(self.calls("POST", "/v1/orders")), 1)

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
		self.assertIn(NOT_CONFIGURED_ALERT_KEY, self.alert_keys())

	def test_webhook_without_secret_alerts_only_where_razorpay_is_used(self):
		self.hide_site_transactions("Razorpay")
		body = webhook_body("payment.captured", payment_entity("order_X", 1000))

		# Razorpay was never used on this site: an anonymous post is refused quietly.
		configure_gateway(gateway="PayU", webhook_secret="")
		self.assertEqual(handle_razorpay_webhook(body, None), (400, {"status": "not_configured"}))
		self.assertNotIn(NOT_CONFIGURED_ALERT_KEY, self.alert_keys())

		# Payments taken through Razorpay before a switch still report to this URL.
		configure_gateway()
		self.start()
		configure_gateway(gateway="PayU", webhook_secret="")
		self.assertEqual(handle_razorpay_webhook(body, None), (400, {"status": "not_configured"}))
		self.assertIn(NOT_CONFIGURED_ALERT_KEY, self.alert_keys())

	def test_bad_signature_naming_no_order_of_ours_is_refused_without_an_alert(self):
		result = self.start()
		for body in (
			webhook_body("payment.captured", payment_entity("order_NOTOURS", 1000)),
			b'{"payload": {"payment": "not an entity"}}',
			b"[1, 2, 3]",
			b"not json",
			# Names our order, but no real event is this large; it is not even parsed.
			webhook_body("payment.captured", payment_entity(result["checkout"]["order_id"], 150000))
			+ b" " * MAX_UNVERIFIED_BODY_BYTES,
		):
			self.assertEqual(handle_razorpay_webhook(body, "f" * 64), (400, {"status": "invalid_signature"}))
		self.assertEqual(self.alert_keys(), [])

		# A correctly signed body that is not an event is refused rather than failing.
		self.assertEqual(
			handle_razorpay_webhook(b"[1]", webhook_signature(b"[1]")), (400, {"status": "invalid_payload"})
		)

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

	def test_switching_payments_off_still_confirms_payments_in_flight(self):
		paid = self.start()
		payment = self.api.pay(paid["checkout"]["order_id"])
		other = make_invoice(self.student, 300)
		missed = self.start([other.name])
		self.api.pay(missed["checkout"]["order_id"])
		self.age(missed["transaction"], 30)

		configure_gateway(enabled=0)
		outcome = confirm_checkout(paid["transaction"], self.checkout_payload(paid, payment))
		self.assertEqual(outcome["status"], "Success")
		self.assertTrue(outcome["receipt_number"])

		result = reconcile_pending_transactions()
		self.assertEqual(result["checked"], 1)
		self.assertEqual(frappe.db.get_value(TRANSACTION, missed["transaction"], "status"), "Success")
		self.assertGreaterEqual(daily_reconciliation()["verified"], 2)

		# New payments stay closed while collection is switched off.
		with self.assertRaises(GatewayNotConfiguredError):
			self.start([make_invoice(self.student, 100).name])

	def test_reconciliation_skips_without_keys(self):
		result = self.start()
		self.age(result["transaction"], 120)
		configure_gateway(enabled=0, key_secret="", payu_salt="")
		self.api.calls.clear()

		self.assertEqual(reconcile_pending_transactions(), {"skipped": True})
		self.assertEqual(daily_reconciliation()["verified"], 0)
		self.assertEqual(self.api.calls, [])
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Initiated")

		# Without keys nothing can be checked, so the payer is not promised an automatic receipt.
		payment = payment_entity(result["checkout"]["order_id"], 150000)
		outcome = confirm_checkout(result["transaction"], self.checkout_payload(result, payment))
		self.assertEqual(outcome["status"], "Pending")
		self.assertNotIn("automatically", outcome["message"])

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

	# -- payments arriving after a transaction was closed ------------------------------------

	def test_capture_after_local_close_is_receipted(self):
		result = self.start()
		order_id = result["checkout"]["order_id"]
		self.api.pay(order_id, status="failed", error_description="Insufficient balance")
		self.age(result["transaction"], 120)
		reconcile_pending_transactions()
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Failed")

		# The payer retries in the checkout that is still open; the callback and webhook are lost.
		self.api.pay(order_id)
		reconcile_pending_transactions()
		self.assertEqual(
			frappe.db.get_value(TRANSACTION, result["transaction"], "status"),
			"Failed",
			"a transaction checked within the hour is not asked again yet",
		)

		frappe.db.set_value(
			TRANSACTION, result["transaction"], "last_checked_on", add_to_date(now_datetime(), hours=-2)
		)
		outcome = reconcile_pending_transactions()
		self.assertEqual(outcome.get("recovered"), 1)
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Success")
		self.assertEqual(len(self.receipts_for(result["transaction"])), 1)
		self.assertEqual(frappe.db.get_value(INVOICE, self.invoice.name, "status"), "Paid")

	def test_staff_can_check_a_closed_transaction(self):
		result = self.start()
		self.age(result["transaction"], 120)
		reconcile_pending_transactions()
		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual(tx.status, "Expired")

		self.assertEqual(tx.check_with_gateway()["status"], "Expired")
		self.api.pay(result["checkout"]["order_id"])
		outcome = tx.check_with_gateway()
		self.assertEqual(outcome["status"], "Success")
		self.assertTrue(outcome["payment"])

	def test_authorised_payment_on_closed_transaction_is_not_captured(self):
		result = self.start()
		self.age(result["transaction"], 120)
		reconcile_pending_transactions()
		payment = self.api.pay(result["checkout"]["order_id"], status="authorized")
		frappe.get_doc(TRANSACTION, result["transaction"]).check_with_gateway()

		self.assertEqual(self.calls("POST", f"/payments/{payment['id']}/capture"), [])
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Expired")
		self.assertIn(f"payment-authorised-not-captured-{result['transaction']}", self.alert_keys())

	def test_payer_told_when_authorised_payment_hit_a_closed_request(self):
		result = self.start()
		self.age(result["transaction"], 120)
		reconcile_pending_transactions()
		late = self.api.pay(result["checkout"]["order_id"], status="authorized")

		outcome = confirm_checkout(result["transaction"], self.checkout_payload(result, late))
		self.assertEqual(outcome["status"], "Expired")
		self.assertIn("returned", outcome["message"])
		self.assertEqual(self.calls("POST", f"/payments/{late['id']}/capture"), [])

	def test_checkout_closes_before_the_transaction_expires(self):
		checkout = self.start()["checkout"]
		self.assertGreater(checkout["timeout"], 0)
		self.assertLessEqual(checkout["timeout"], 60 * 60)

	# -- concurrent and repeated confirmations -----------------------------------------------

	def test_successful_delivery_resolves_earlier_webhook_error(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"])
		self.deliver("payment.captured", payment)
		resolved = [call.args[0] for call in self.resolved.call_args_list if call.args]
		self.assertIn(f"payment-webhook-error-{result['transaction']}", resolved)

	def test_second_capture_on_a_paid_order_is_alerted_not_ignored(self):
		result = self.start()
		order_id = result["checkout"]["order_id"]
		first = self.api.pay(order_id)
		self.deliver("payment.captured", first)
		receipt = self.receipts_for(result["transaction"])[0]

		second = self.api.pay(order_id)
		status, response = self.deliver("payment.captured", second)
		self.assertEqual((status, response["result"]), (200, "Success"))
		self.assertEqual(self.receipts_for(result["transaction"]), [receipt])

		alert = next(
			call
			for call in self.alerts.call_args_list
			if call.args and call.args[0] == f"payment-multiple-captures-{result['transaction']}"
		)
		self.assertEqual(alert.kwargs["details"]["receipted_payment_id"], first["id"])
		self.assertEqual(alert.kwargs["details"]["extra_payment_id"], second["id"])
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_name": result["transaction"], "content": ("like", f"%{second['id']}%")},
			)
		)

		# The second payer's own callback gets the existing receipt, and raises the same alert.
		self.alerts.reset_mock()
		outcome = confirm_checkout(result["transaction"], self.checkout_payload(result, second))
		self.assertEqual(outcome["receipt_number"], receipt)
		self.assertIn(f"payment-multiple-captures-{result['transaction']}", self.alert_keys())

	def test_authorised_second_payment_is_never_captured(self):
		result = self.start()
		order_id = result["checkout"]["order_id"]
		self.deliver("payment.captured", self.api.pay(order_id))
		late = self.api.pay(order_id, status="authorized")

		outcome = confirm_checkout(result["transaction"], self.checkout_payload(result, late))
		self.assertEqual(outcome["status"], "Success")
		self.assertEqual(self.calls("POST", f"/payments/{late['id']}/capture"), [])

	def test_capture_skipped_when_order_already_has_a_captured_payment(self):
		result = self.start()
		order_id = result["checkout"]["order_id"]
		authorised = self.api.pay(order_id, status="authorized")
		self.api.pay(order_id)  # captured, but neither its callback nor its webhook arrived yet

		outcome = confirm_checkout(result["transaction"], self.checkout_payload(result, authorised))
		self.assertEqual(self.calls("POST", f"/payments/{authorised['id']}/capture"), [])
		self.assertEqual(outcome["status"], "Pending")
		self.assertIn(f"payment-authorised-not-captured-{result['transaction']}", self.alert_keys())

	def test_daily_verification_finds_extra_capture_and_refunds(self):
		result = self.start()
		order_id = result["checkout"]["order_id"]
		first = self.api.pay(order_id)
		self.deliver("payment.captured", first)
		self.api.pay(order_id)  # an extra capture whose webhook never arrived

		self.alerts.reset_mock()
		daily_reconciliation()
		self.assertFalse(frappe.db.get_value(TRANSACTION, result["transaction"], "reconciled_on"))
		self.assertIn(f"payment-multiple-captures-{result['transaction']}", self.alert_keys())

		# A partial refund from the dashboard leaves the payment "captured" at Razorpay.
		other = make_invoice(self.student, 500)
		refunded = self.start([other.name])
		partly = self.api.pay(
			refunded["checkout"]["order_id"], amount_refunded=10000, refund_status="partial"
		)
		self.deliver("payment.captured", partly)
		self.alerts.reset_mock()
		daily = daily_reconciliation()
		self.assertGreaterEqual(daily["issues"], 1)
		tx = frappe.get_doc(TRANSACTION, refunded["transaction"])
		self.assertEqual((tx.refunded_amount, tx.reconciled_on), (100, None))
		self.assertIn(f"payment-refunded-{refunded['transaction']}", self.alert_keys())

	def test_refund_webhook_is_recorded(self):
		result = self.start()
		payment = self.api.pay(result["checkout"]["order_id"])
		self.deliver("payment.captured", payment)

		payment.update(amount_refunded=50000, refund_status="partial")
		status, response = self.deliver("refund.processed", payment)
		self.assertEqual((status, response["status"]), (200, "ok"))
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "refunded_amount"), 500)
		self.assertIn(f"payment-refunded-{result['transaction']}", self.alert_keys())

		rows = gateway_reconciliation({"from_date": nowdate(), "to_date": nowdate(), "only_issues": 1})[1]
		row = next(r for r in rows if r.transaction == result["transaction"])
		self.assertIn("refunded", row.issue)

	# -- orders that can no longer be read ---------------------------------------------------

	def test_test_mode_order_closes_after_switching_to_live_keys(self):
		result = self.start()
		self.age(result["transaction"], 120)
		configure_gateway(live=True)
		self.api.calls.clear()

		outcome = reconcile_pending_transactions()
		self.assertEqual(outcome["errors"], 0)
		self.assertEqual(self.api.calls, [], "a test order is never looked up with live keys")
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Expired")
		self.assertNotIn("payment-gateway-unreachable", self.alert_keys())

	def test_unreadable_order_is_flagged_not_counted_as_outage(self):
		result = self.start()
		self.age(result["transaction"], 30)
		self.api.unreadable_orders[result["checkout"]["order_id"]] = FakeResponse(
			400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "The id provided does not exist"}}
		)
		outcome = reconcile_pending_transactions()
		self.assertGreaterEqual(outcome["unreadable"], 1)
		self.assertEqual(outcome["unreachable"], 0)
		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual(tx.status, "Initiated")
		self.assertTrue(tx.last_checked_on)
		self.assertIn("does not exist", tx.failure_reason)
		self.assertIn(f"payment-order-unreadable-{tx.name}", self.alert_keys())

		tx.close_as_expired("Checked the dashboard: no payment on this order")
		self.assertEqual(frappe.db.get_value(TRANSACTION, tx.name, "status"), "Expired")

	def test_unreachable_gateway_raises_one_alert(self):
		for amount in (100, 200, 300):
			self.age(self.start([make_invoice(self.student, amount).name])["transaction"], 30)

		def timeout(*args, **kwargs):
			raise requests.Timeout("read timed out")

		with patch("hostel360.hostel_fees.gateways.razorpay.requests.request", side_effect=timeout):
			outcome = reconcile_pending_transactions()
		self.assertGreaterEqual(outcome["unreachable"], 3)
		self.assertIn("payment-gateway-unreachable", self.alert_keys())

	# -- test-mode payments ------------------------------------------------------------------

	def test_test_mode_payments_limited_to_test_payers(self):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"{frappe.generate_hash(length=8)}@example.com",
				"first_name": "Payer",
			}
		).insert(ignore_permissions=True)
		with patch("hostel360.hostel_fees.services.assert_can_access_student"):
			with self.assertRaises(frappe.ValidationError):
				create_payment_order(self.student, [self.invoice.name], user.name)
			self.assertFalse(online_payment_available(self.student, user.name))

			configure_gateway(test_payers=[self.student])
			self.assertTrue(online_payment_available(self.student, user.name))
			result = create_payment_order(self.student, [self.invoice.name], user.name)

		self.deliver("payment.captured", self.api.pay(result["checkout"]["order_id"]))
		receipt = frappe.get_doc(PAYMENT, self.receipts_for(result["transaction"])[0])
		self.assertEqual(receipt.is_test, 1)
		self.assertFalse(self.notified("payment_received", receipt.name))
		html = frappe.get_print(PAYMENT, receipt.name, print_format="Hostel Fee Receipt")
		self.assertIn("not a valid receipt", html)
		collected = fee_collection({"from_date": nowdate(), "to_date": nowdate(), "student": self.student})[1]
		self.assertNotIn(receipt.name, [row["payment"] for row in collected])

		# Live payments are open to every student.
		configure_gateway(live=True)
		self.assertTrue(online_payment_available(make_student(), user.name))
