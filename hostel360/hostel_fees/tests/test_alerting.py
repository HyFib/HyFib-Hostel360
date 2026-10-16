"""Fee alerts never break payment work, except a deadlock, which must reach the caller.

When the operations module writes an alert inside the caller's transaction (no second database
connection to be had), a deadlock there has already rolled the caller's work back. Hiding it
would let the caller commit only what it writes afterwards.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.api import payments as payment_api
from hostel360.hostel_fees import alerting
from hostel360.hostel_fees.gateways.base import GatewayError
from hostel360.hostel_fees.reconciliation import _Run

OPS_RAISE = "hostel360.hostel_operations.alerts.raise_alert"
OPS_RESOLVE = "hostel360.hostel_operations.alerts.resolve_alert"


class TestFeeAlerting(IntegrationTestCase):
	def test_deadlock_reaches_the_caller(self):
		with patch(OPS_RAISE, side_effect=frappe.QueryDeadlockError("deadlock")):
			with self.assertRaises(frappe.QueryDeadlockError):
				alerting.raise_alert("fee-test-deadlock", "Deadlocked alert")
		with patch(OPS_RESOLVE, side_effect=frappe.QueryDeadlockError("deadlock")):
			with self.assertRaises(frappe.QueryDeadlockError):
				alerting.resolve_alert("fee-test-deadlock")

	def test_other_failures_are_logged_not_raised(self):
		with (
			patch(OPS_RAISE, side_effect=RuntimeError("alert store unavailable")),
			patch("frappe.log_error") as log_error,
		):
			self.assertIsNone(alerting.raise_alert("fee-test-broken", "Broken alert"))
		self.assertIn("fee-test-broken", log_error.call_args.kwargs["title"])

		with (
			patch(OPS_RESOLVE, side_effect=frappe.ValidationError("bad key")),
			patch("frappe.log_error") as log_error,
		):
			self.assertFalse(alerting.resolve_alert("fee-test-broken"))
		self.assertTrue(log_error.called)

	def test_results_are_passed_through(self):
		with patch(OPS_RAISE, return_value="OPS-ALERT-0001") as ops_raise:
			self.assertEqual(alerting.raise_alert("fee-test-ok", "Alert", details={"a": 1}), "OPS-ALERT-0001")
		self.assertEqual(ops_raise.call_args.kwargs["source"], "App")
		with patch(OPS_RESOLVE, return_value=True):
			self.assertTrue(alerting.resolve_alert("fee-test-ok"))

	def test_reconciliation_carries_on_after_an_alert_deadlock(self):
		run = _Run()
		unreadable = GatewayError("order not found", http_status=400, code="BAD_REQUEST_ERROR")

		def settle(name):
			raise unreadable

		with (
			patch(
				"hostel360.hostel_fees.reconciliation._record_unreadable",
				side_effect=frappe.QueryDeadlockError("deadlock"),
			),
			patch.object(frappe.db, "rollback") as rollback,
			patch("frappe.log_error") as log_error,
		):
			run.attempt("PTX-TEST-1", settle)
			run.attempt("PTX-TEST-2", settle)

		self.assertEqual(run.unreadable, ["PTX-TEST-1", "PTX-TEST-2"])
		self.assertEqual(run.errors, [])
		self.assertTrue(any(not call.kwargs for call in rollback.call_args_list))
		self.assertEqual(log_error.call_count, 2)

	def test_payu_return_still_sends_the_payer_to_their_fees_page(self):
		previous = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(txnid="H360TEST123", status="success")
		try:
			with (
				patch(
					"hostel360.hostel_fees.services.confirm_gateway_return",
					side_effect=frappe.QueryDeadlockError("deadlock"),
				),
				patch.object(frappe.db, "rollback") as rollback,
				patch("frappe.log_error") as log_error,
			):
				response = payment_api.payu_return()
		finally:
			frappe.local.form_dict = previous

		self.assertEqual(response.status_code, 303)
		self.assertEqual(response.headers["Location"], "/hostel/fees?status=pending")
		self.assertTrue(rollback.called)
		self.assertTrue(log_error.called)
