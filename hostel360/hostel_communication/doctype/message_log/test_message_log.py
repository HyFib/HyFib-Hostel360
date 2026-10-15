# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime
from frappe.utils.password import encrypt

from hostel360.hostel_communication.providers.base import DeliveryResult
from hostel360.setup.roles import OPERATIONS_ROLE


def failed_log(**values) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Message Log",
			"channel": "SMS",
			"event": "fee_reminder",
			"status": "Failed",
			"recipient": "98xxxxxx10",
			"attempts": 1,
			"error": "HTTP 503",
			"payload": json.dumps({"to": encrypt("9876543210"), "body": "Reminder", "parameters": []}),
			"next_retry_on": now_datetime(),
			**values,
		}
	).insert(ignore_permissions=True)
	return doc.name


class TestMessageLog(IntegrationTestCase):
	def test_retry_now_resends_and_clears_payload(self):
		name = failed_log()
		doc = frappe.get_doc("Message Log", name)
		doc.run_method("onload")
		self.assertTrue(doc.get_onload().can_retry)
		with patch(
			"hostel360.hostel_communication.providers.sms.send_sms",
			return_value=DeliveryResult.sent("MSG91", "req-77"),
		) as send_sms:
			result = doc.retry_now()
		self.assertTrue(result["ok"])
		self.assertEqual(send_sms.call_args.args[0], "9876543210")
		row = frappe.db.get_value(
			"Message Log", name, ["status", "payload", "provider_message_id"], as_dict=True
		)
		self.assertEqual((row.status, row.provider_message_id), ("Sent", "req-77"))
		self.assertIsNone(row.payload)

	def test_retry_now_needs_operations_or_admin_role(self):
		name = failed_log()
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"log-{frappe.generate_hash(length=6)}@example.com",
				"first_name": "Viewer",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc("Message Log", name)
		with self.set_user(user.name), self.assertRaises(frappe.PermissionError):
			doc.retry_now()

		user.add_roles(OPERATIONS_ROLE)
		with (
			self.set_user(user.name),
			patch(
				"hostel360.hostel_communication.providers.sms.send_sms",
				return_value=DeliveryResult.failed("MSG91", "still down", retryable=True),
			),
		):
			self.assertFalse(doc.retry_now()["ok"])

	def test_sent_messages_cannot_be_retried(self):
		name = failed_log(status="Sent", payload=None)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("Message Log", name).retry_now()

	def test_old_logs_are_cleared(self):
		from hostel360.hostel_communication.doctype.message_log.message_log import MessageLog

		old = failed_log(status="Sent", payload=None)
		frappe.db.set_value(
			"Message Log", old, "creation", add_days(now_datetime(), -400), update_modified=False
		)
		recent = failed_log(status="Sent", payload=None)
		MessageLog.clear_old_logs(days=365)
		self.assertFalse(frappe.db.exists("Message Log", old))
		self.assertTrue(frappe.db.exists("Message Log", recent))
