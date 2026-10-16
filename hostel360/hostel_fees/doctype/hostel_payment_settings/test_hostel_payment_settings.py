# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe

from hostel360.hostel_fees.constants import SETTINGS
from hostel360.hostel_fees.gateways import is_gateway_configured
from hostel360.hostel_fees.tests import fixtures as fee_fixtures
from hostel360.hostel_fees.tests.fixtures import (
	TEST_KEY_ID,
	FakeRazorpay,
	FeeTestCase,
	configure_gateway,
)

# Read by the Frappe test runner: fee tests build every linked record they need themselves.
IGNORE_TEST_RECORD_DEPENDENCIES = fee_fixtures.IGNORE_TEST_RECORD_DEPENDENCIES


class TestHostelPaymentSettings(FeeTestCase):
	def test_keys_must_match_mode(self):
		settings = configure_gateway()
		settings.key_id = "rzp_live_ABCDEF123456"
		with self.assertRaises(frappe.ValidationError):
			settings.save()

		settings.reload()
		settings.test_mode = 0
		with self.assertRaises(frappe.ValidationError):
			settings.save()

	def test_enabling_requires_keys(self):
		configure_gateway(enabled=0)
		settings = frappe.get_single(SETTINGS)
		settings.key_secret = ""
		settings.save()
		self.assertFalse(is_gateway_configured())

		settings.enabled = 1
		with self.assertRaises(frappe.ValidationError):
			settings.save()

	def test_expiry_bounds(self):
		settings = configure_gateway()
		settings.transaction_expiry_minutes = 5
		with self.assertRaises(frappe.ValidationError):
			settings.save()

	def test_configured_flag_follows_enabled(self):
		configure_gateway(enabled=1)
		self.assertTrue(is_gateway_configured())
		configure_gateway(enabled=0)
		self.assertFalse(is_gateway_configured())

	def test_connection_check(self):
		configure_gateway(enabled=0)
		api = FakeRazorpay()
		with patch("hostel360.hostel_fees.gateways.razorpay.requests.request", side_effect=api):
			result = frappe.get_single(SETTINGS).test_connection()
		self.assertTrue(result["ok"])
		self.assertEqual(result["mode"], "test")
		self.assertEqual(api.calls[0]["method"], "GET")
		self.assertEqual(api.calls[0]["auth"][0], TEST_KEY_ID)

		doc = frappe.get_single(SETTINGS)
		doc.run_method("onload")
		onload = doc.get_onload()
		self.assertTrue(onload.webhook_url.endswith("/api/method/hostel360.api.payments.razorpay_webhook"))
		self.assertIn("refund.processed", onload.webhook_events)
		self.assertTrue(onload.payu_return_url.endswith("/api/method/hostel360.api.payments.payu_return"))
		self.assertTrue(onload.payu_webhook_url.endswith("/api/method/hostel360.api.payments.payu_webhook"))

	def test_gateway_choice(self):
		options = frappe.get_meta(SETTINGS).get_field("gateway").options.split("\n")
		self.assertEqual(options, ["Razorpay", "PayU"])
		settings = configure_gateway(gateway="PayU")
		self.assertTrue(is_gateway_configured())

		settings.reload()
		settings.key_secret = ""
		settings.gateway = "Razorpay"
		with self.assertRaises(frappe.ValidationError):
			settings.save()
