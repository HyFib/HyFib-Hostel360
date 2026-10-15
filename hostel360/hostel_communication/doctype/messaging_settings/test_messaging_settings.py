# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase


class TestMessagingSettings(IntegrationTestCase):
	def settings(self, **values):
		doc = frappe.get_doc("Messaging Settings")
		doc.update({"sms_enabled": 0, "whatsapp_enabled": 0, "test_mode": 1, **values})
		return doc

	def test_defaults_are_installed(self):
		doc = frappe.get_doc("Messaging Settings")
		self.assertEqual(doc.otp_length, 6)
		self.assertEqual(doc.otp_expiry_seconds, 300)
		self.assertEqual(doc.otp_max_attempts, 5)
		self.assertEqual(doc.otp_resend_cooldown_seconds, 60)
		self.assertEqual(doc.otp_max_sends_per_hour, 5)
		self.assertEqual(doc.whatsapp_api_version, "v21.0")

	def test_numeric_limits(self):
		with self.assertRaises(frappe.ValidationError):
			self.settings(otp_length=12).save()
		with self.assertRaises(frappe.ValidationError):
			self.settings(otp_expiry_seconds=10).save()
		doc = self.settings(otp_max_attempts=0)
		doc.save()
		self.assertEqual(doc.otp_max_attempts, 5)

	def test_trusted_networks_are_validated(self):
		with self.assertRaises(frappe.ValidationError):
			self.settings(otp_trusted_networks="10.0.0.0/8\nnot-a-network").save()
		doc = self.settings(otp_trusted_networks=" 10.20.0.0/16 \n\n117.239.44.10\n")
		doc.save()
		self.assertEqual(doc.otp_trusted_networks, "10.20.0.0/16\n117.239.44.10")

	def test_sender_id_and_extra_parameters(self):
		with self.assertRaises(frappe.ValidationError):
			self.settings(sms_sender_id="ACG-CET").save()
		with self.assertRaises(frappe.ValidationError):
			self.settings(sms_extra_params="[1, 2]").save()
		doc = self.settings(sms_sender_id="acgcet", sms_extra_params='{"route": "4"}')
		doc.save()
		self.assertEqual(doc.sms_sender_id, "ACGCET")

	def test_enabling_channels_needs_credentials(self):
		with self.assertRaises(frappe.ValidationError):
			self.settings(sms_enabled=1, sms_auth_key="").save()
		with self.assertRaises(frappe.ValidationError):
			self.settings(whatsapp_enabled=1, whatsapp_phone_number_id="123", whatsapp_access_token="").save()
		with self.assertRaises(frappe.ValidationError):
			self.settings(whatsapp_phone_number_id="not-a-number").save()
		with self.assertRaises(frappe.ValidationError):
			self.settings(whatsapp_api_version="21").save()

	def test_gateway_urls_must_use_https_in_production(self):
		with patch.dict(frappe.conf, {"developer_mode": 0}):
			with self.assertRaises(frappe.ValidationError):
				self.settings(sms_api_url="http://sms.example.com/send").save()
			self.settings(sms_api_url="https://sms.example.com/send").save()

	def test_test_message_requires_role(self):
		from hostel360.hostel_communication.doctype.messaging_settings.messaging_settings import (
			MessagingSettings,
		)

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"settings-{frappe.generate_hash(length=6)}@example.com",
				"first_name": "No",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc("Messaging Settings")
		self.assertIsInstance(doc, MessagingSettings)
		with self.set_user(user.name), self.assertRaises(frappe.PermissionError):
			doc.send_test_message("SMS", "9876543210")

	def test_test_message_goes_through_send_direct(self):
		doc = frappe.get_doc("Messaging Settings")
		with patch("hostel360.hostel_communication.dispatch.send_direct", return_value=True) as send_direct:
			result = doc.send_test_message("sms", "9876543210")
		self.assertTrue(result["ok"])
		self.assertEqual(send_direct.call_args.args[:2], ("SMS", "9876543210"))
		frappe.cache.delete(frappe.cache.make_key(f"hostel360:messaging:test_messages:{frappe.session.user}"))
