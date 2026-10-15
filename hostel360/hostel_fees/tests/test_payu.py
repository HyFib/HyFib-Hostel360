"""PayU hosted checkout: signed form, return post, webhook, verify API and reconciliation.

PayU's HTTP API is replaced by ``FakePayU``; request and response hashes are computed in the
fixtures from PayU's published formulas, independently of the adapter.
"""

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from hostel360.api import payments as payment_api
from hostel360.hostel_fees.constants import INVOICE, PAYMENT, SETTINGS, TRANSACTION
from hostel360.hostel_fees.gateways import GatewayError, get_gateway
from hostel360.hostel_fees.gateways.base import CAPTURED, CREATED, FAILED, REFUNDED
from hostel360.hostel_fees.gateways.payu import payu_status
from hostel360.hostel_fees.reconciliation import reconcile_pending_transactions
from hostel360.hostel_fees.services import create_payment_order
from hostel360.hostel_fees.tests.fixtures import (
	PAYU_KEY,
	PAYU_SALT,
	FakePayU,
	FakeRazorpay,
	FeeTestCase,
	configure_gateway,
	make_hostel,
	make_invoice,
	make_student,
	payu_request_hash,
	payu_response_hash,
	payu_sha512,
)
from hostel360.hostel_fees.webhooks import handle_payu_webhook

RETURN_URL_SUFFIX = "/api/method/hostel360.api.payments.payu_return"


class TestPayUGateway(FeeTestCase):
	def setUp(self):
		super().setUp()
		self.payu = FakePayU()
		self.razorpay = FakeRazorpay()
		for target, fake in (
			("hostel360.hostel_fees.gateways.payu.requests.post", self.payu),
			("hostel360.hostel_fees.gateways.razorpay.requests.request", self.razorpay),
		):
			patcher = patch(target, side_effect=fake)
			patcher.start()
			self.addCleanup(patcher.stop)

		configure_gateway(gateway="PayU")
		self.student = make_student(hostel=make_hostel())
		self.invoice = make_invoice(self.student, 1500)

	# -- helpers -----------------------------------------------------------------------------

	def start(self, invoices=None) -> dict:
		return create_payment_order(self.student, invoices or [self.invoice.name], "Administrator", "203.0.113.9")

	def post_return(self, fields: dict):
		"""Post ``fields`` to the return URL as PayU does; returns (response, query)."""
		previous = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(fields, cmd="hostel360.api.payments.payu_return")
		try:
			response = payment_api.payu_return()
		finally:
			frappe.local.form_dict = previous
		location = urlparse(response.headers["Location"])
		query = {key: values[0] for key, values in parse_qs(location.query).items()}
		self.assertEqual(response.status_code, 303)
		self.assertEqual(location.path, "/hostel/fees")
		return response, query

	def verify_calls(self) -> list[dict]:
		return [c["data"] for c in self.payu.calls if c["data"].get("command") == "verify_payment"]

	def receipts_for(self, transaction: str) -> list[str]:
		return frappe.get_all(PAYMENT, filters={"payment_transaction": transaction, "docstatus": 1}, pluck="name")

	def age(self, transaction: str, minutes: int):
		frappe.db.set_value(
			TRANSACTION, transaction, "creation", add_to_date(now_datetime(), minutes=-minutes), update_modified=False
		)

	# -- checkout form -----------------------------------------------------------------------

	def test_checkout_form_is_signed_per_specification(self):
		result = self.start()
		self.assertEqual(self.payu.calls, [], "PayU has no order API; nothing is called before checkout")

		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual((tx.gateway, tx.status, tx.amount), ("PayU", "Initiated", 1500))
		self.assertEqual(result["gateway"], "payu")
		self.assertEqual(result["amount"], 1500)

		checkout = result["checkout"]
		self.assertEqual((checkout["action_url"], checkout["method"]), ("https://test.payu.in/_payment", "POST"))
		fields = checkout["fields"]
		self.assertEqual(fields["key"], PAYU_KEY)
		self.assertEqual(fields["txnid"], tx.gateway_order_id)
		self.assertLessEqual(len(fields["txnid"]), 25)
		self.assertRegex(fields["txnid"], r"^[A-Za-z0-9]+$")
		self.assertEqual(fields["amount"], "1500.00")
		self.assertEqual(fields["udf1"], tx.name)
		self.assertTrue(fields["surl"].endswith(RETURN_URL_SUFFIX))
		self.assertEqual(fields["surl"], fields["furl"])
		self.assertRegex(fields["phone"], r"^\d{10}$")
		self.assertIn("@", fields["email"])
		self.assertTrue(fields["firstname"] and "|" not in fields["productinfo"])
		self.assertEqual(fields["hash"], payu_request_hash(fields))

	def test_live_mode_posts_to_secure_payu(self):
		configure_gateway(gateway="PayU", live=True)
		checkout = self.start()["checkout"]
		self.assertEqual(checkout["action_url"], "https://secure.payu.in/_payment")

	def test_every_order_gets_a_fresh_txnid(self):
		first = self.start()["checkout"]["fields"]["txnid"]
		second = self.start([make_invoice(self.student, 100).name])["checkout"]["fields"]["txnid"]
		self.assertNotEqual(first, second)

	# -- return post -------------------------------------------------------------------------

	def test_successful_return_issues_one_receipt_and_redirects(self):
		result = self.start()
		record = self.payu.pay(result["checkout"])
		fields = self.payu.return_fields(result["checkout"], record)

		_response, query = self.post_return(fields)
		self.assertEqual(query, {"payment": result["transaction"], "status": "success"})

		call = self.verify_calls()[-1]
		txnid = result["checkout"]["fields"]["txnid"]
		self.assertEqual(call["var1"], txnid)
		self.assertEqual(call["hash"], payu_sha512(PAYU_KEY, "verify_payment", txnid, PAYU_SALT))

		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual(
			(tx.status, tx.gateway_payment_id, tx.payment_method), ("Success", record["mihpayid"], "upi")
		)
		receipts = self.receipts_for(tx.name)
		self.assertEqual(receipts, [tx.payment])
		receipt = frappe.get_doc(PAYMENT, tx.payment)
		self.assertEqual(
			(receipt.mode_of_payment, receipt.amount, receipt.reference_no), ("Online", 1500, record["mihpayid"])
		)
		self.assertEqual(frappe.db.get_value(INVOICE, self.invoice.name, "status"), "Paid")
		self.assertNotIn(fields["email"], tx.gateway_response or "")

		# The same post again (a refresh), then the webhook: still exactly one receipt.
		self.assertEqual(self.post_return(fields)[1]["status"], "success")
		status, response = handle_payu_webhook(fields)
		self.assertEqual((status, response["status"]), (200, "ok"))
		self.assertEqual(self.receipts_for(tx.name), receipts)

	def test_return_works_without_a_session(self):
		# With SameSite=Lax cookies PayU's cross-site post arrives as a guest request.
		result = self.start()
		fields = self.payu.return_fields(result["checkout"], self.payu.pay(result["checkout"]))
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")

		_response, query = self.post_return(fields)
		self.assertEqual(query["status"], "success")
		self.assertEqual(frappe.session.user, "Guest")
		receipt = frappe.db.get_value(TRANSACTION, result["transaction"], "payment")
		self.assertEqual(frappe.db.get_value(PAYMENT, receipt, "owner"), "Administrator")

	def test_tampered_return_is_refused(self):
		result = self.start()
		record = self.payu.pay(result["checkout"])
		fields = self.payu.return_fields(result["checkout"], record)
		fields["amount"] = "1.00"

		_response, query = self.post_return(fields)
		self.assertEqual(query["status"], "pending")
		self.assertEqual(self.verify_calls(), [])
		self.assertEqual(self.receipts_for(result["transaction"]), [])
		self.assertIn("payu-return-signature", self.alert_keys())
		self.assertTrue(self.alerts_in("Security"))

	def test_posted_status_is_only_a_hint(self):
		result = self.start()
		record = self.payu.pay(result["checkout"], status="pending", unmapped="pending")
		# A correctly signed "success" post, while PayU's own record says the bank has not confirmed.
		fields = self.payu.return_fields(result["checkout"], record, status="success")

		_response, query = self.post_return(fields)
		self.assertEqual(query["status"], "pending")
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Pending")
		self.assertEqual(self.receipts_for(result["transaction"]), [])

	def test_failed_return_closes_transaction_and_frees_dues(self):
		result = self.start()
		record = self.payu.pay(result["checkout"], status="failure", unmapped="failed")
		_response, query = self.post_return(self.payu.return_fields(result["checkout"], record))

		self.assertEqual(query, {"payment": result["transaction"], "status": "failed"})
		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual((tx.status, tx.failure_reason), ("Failed", "Transaction declined"))

		# A PayU txnid is used once: paying again starts a new transaction.
		retry = self.start()
		self.assertNotEqual(retry["transaction"], result["transaction"])
		self.assertNotEqual(retry["checkout"]["fields"]["txnid"], result["checkout"]["fields"]["txnid"])

	def test_amount_mismatch_is_rejected(self):
		result = self.start()
		record = self.payu.pay(result["checkout"], amount="1.00")
		_response, query = self.post_return(self.payu.return_fields(result["checkout"], record))

		self.assertEqual(query["status"], "failed")
		self.assertEqual(self.receipts_for(result["transaction"]), [])
		tx = frappe.get_doc(TRANSACTION, result["transaction"])
		self.assertEqual(tx.status, "Failed")
		self.assertIn("amount mismatch", tx.failure_reason)
		self.assertTrue(self.alerts_in("Payments"))

	def test_unknown_transaction_redirects_as_failed(self):
		result = self.start()
		fields = self.payu.return_fields(result["checkout"], txnid="NOTOURS12345")
		_response, query = self.post_return(fields)
		self.assertEqual(query, {"status": "failed"})

	def test_unreachable_verify_api_leaves_payment_for_reconciliation(self):
		result = self.start()
		record = self.payu.pay(result["checkout"])
		self.payu.fail_next = requests.Timeout("read timed out")
		_response, query = self.post_return(self.payu.return_fields(result["checkout"], record))
		self.assertEqual(query["status"], "pending")
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Initiated")

		self.age(result["transaction"], 15)
		reconcile_pending_transactions()
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Success")

	def test_success_after_local_failure_is_still_receipted(self):
		result = self.start()
		record = self.payu.pay(result["checkout"], status="failure", unmapped="failed")
		self.post_return(self.payu.return_fields(result["checkout"], record))
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Failed")

		# The bank confirms after all; PayU posts the webhook for the same txnid.
		record = self.payu.pay(result["checkout"])
		status, _response = handle_payu_webhook(self.payu.return_fields(result["checkout"], record))
		self.assertEqual(status, 200)
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Success")
		self.assertEqual(len(self.receipts_for(result["transaction"])), 1)

	# -- reuse of an open order --------------------------------------------------------------

	def test_repeat_request_reuses_txnid_payu_has_not_seen(self):
		first = self.start()
		second = self.start()
		self.assertEqual(first["transaction"], second["transaction"])
		self.assertEqual(first["checkout"]["fields"]["txnid"], second["checkout"]["fields"]["txnid"])

	def test_repeat_request_after_a_failed_attempt_gets_a_new_txnid(self):
		first = self.start()
		self.payu.pay(first["checkout"], status="failure", unmapped="userCancelled")
		second = self.start()
		self.assertNotEqual(first["transaction"], second["transaction"])
		self.assertEqual(frappe.db.get_value(TRANSACTION, first["transaction"], "status"), "Expired")

	def test_repeat_request_while_bank_is_deciding_is_refused(self):
		first = self.start()
		self.payu.pay(first["checkout"], status="pending", unmapped="pending")
		with self.assertRaises(frappe.ValidationError):
			self.start()

	# -- webhook and reconciliation ----------------------------------------------------------

	def test_webhook_verification(self):
		result = self.start()
		record = self.payu.pay(result["checkout"])
		fields = self.payu.return_fields(result["checkout"], record)

		forged = dict(fields, hash="0" * 128)
		self.assertEqual(handle_payu_webhook(forged), (400, {"status": "invalid_signature"}))
		self.assertEqual(self.receipts_for(result["transaction"]), [])

		unknown = self.payu.return_fields(result["checkout"], record, txnid="SOMEONEELSE1")
		self.assertEqual(handle_payu_webhook(unknown)[1]["status"], "ignored")

		status, response = handle_payu_webhook(fields)
		self.assertEqual((status, response["result"]), (200, "Success"))
		self.assertEqual(len(self.receipts_for(result["transaction"])), 1)

	def test_reconciliation_polls_pending_payu_transactions(self):
		result = self.start()
		self.payu.pay(result["checkout"])  # the payer's return post never arrived
		self.age(result["transaction"], 15)

		reconcile_pending_transactions()
		self.assertEqual(frappe.db.get_value(TRANSACTION, result["transaction"], "status"), "Success")
		self.assertEqual(len(self.receipts_for(result["transaction"])), 1)

	def test_switching_gateways_settles_old_payments_with_their_own_gateway(self):
		configure_gateway(gateway="Razorpay")
		old = self.start()
		self.assertEqual(old["gateway"], "razorpay")
		self.razorpay.pay(old["checkout"]["order_id"])
		self.age(old["transaction"], 15)

		configure_gateway(gateway="PayU")
		reconcile_pending_transactions()
		self.assertEqual(frappe.db.get_value(TRANSACTION, old["transaction"], "status"), "Success")

		new = self.start([make_invoice(self.student, 200).name])
		self.assertEqual(new["gateway"], "payu")

	# -- adapter details ---------------------------------------------------------------------

	def test_response_hash_with_additional_charges(self):
		gateway = get_gateway(gateway_name="PayU")
		fields = {
			"key": PAYU_KEY,
			"txnid": "PTX1A2B3C",
			"amount": "1500.00",
			"productinfo": "Hostel dues",
			"firstname": "Fee",
			"email": "fee@example.com",
			"status": "success",
			"udf1": "PTX-2026-000001",
			"additionalCharges": "17.70",
		}
		fields["hash"] = payu_response_hash(fields)
		self.assertTrue(gateway.verify_return(fields))
		self.assertFalse(gateway.verify_return(dict(fields, additionalCharges="0.00")))
		self.assertFalse(gateway.verify_return(dict(fields, key="otherkey")))
		self.assertFalse(gateway.verify_return(dict(fields, hash="")))

	def test_status_mapping(self):
		self.assertEqual(payu_status("success", "captured"), CAPTURED)
		self.assertEqual(payu_status("success", "auth"), CREATED)
		self.assertEqual(payu_status("pending", "pending"), CREATED)
		self.assertEqual(payu_status("failure", "userCancelled"), FAILED)
		self.assertEqual(payu_status("failure", "bounced"), FAILED)
		self.assertEqual(payu_status("success", "autoRefund"), REFUNDED)

	def test_settings_and_connection_check(self):
		settings = frappe.get_single(SETTINGS)
		settings.payu_merchant_salt = ""
		with self.assertRaises(frappe.ValidationError):
			settings.save()

		result = frappe.get_single(SETTINGS).test_connection()
		self.assertTrue(result["ok"])
		self.assertEqual(result["mode"], "test")

		configure_gateway(gateway="PayU", payu_salt="wrong-salt")
		with self.assertRaises(GatewayError) as error:
			get_gateway().test_connection()
		self.assertEqual(error.exception.http_status, 401)
