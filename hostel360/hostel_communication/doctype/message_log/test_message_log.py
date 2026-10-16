# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime
from frappe.utils.password import encrypt

from hostel360.hostel_communication import permissions
from hostel360.hostel_communication.providers.base import DeliveryResult
from hostel360.setup.roles import (
	ADMIN_ROLE,
	AUDITOR_ROLE,
	OFFICE_ROLE,
	OPERATIONS_ROLE,
	WARDEN_ROLE,
)


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


def make_user(*roles: str) -> str:
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": f"log-{frappe.generate_hash(length=8)}@example.com",
			"first_name": "Log",
			"last_name": "Reader",
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	return user.name


def grievance_row(confidential: bool, assigned_to: str | None = None) -> str:
	"""A bare grievance row: only the fields Message Log access depends on matter here."""
	name = f"GRV-T-{frappe.generate_hash(length=8)}"
	frappe.get_doc(
		{
			"doctype": "Hostel Grievance",
			"name": name,
			"subject": "Seniors in room 12",
			"category": "Ragging or Harassment" if confidential else "Plumbing",
			"priority": "Urgent" if confidential else "Low",
			"description": "Test",
			"is_confidential": 1 if confidential else 0,
			"assigned_to": assigned_to,
		}
	).db_insert()
	return name


def grievance_log(grievance: str | None) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Message Log",
			"channel": "SMS",
			"event": "grievance_updated" if grievance else "fee_reminder",
			"status": "Sent",
			"recipient": "98xxxxxx10",
			"recipient_user": "student-t13a4fe1@portal.hostel360.invalid",
			"reference_doctype": "Hostel Grievance" if grievance else None,
			"reference_name": grievance,
			"subject": "Grievance update: Seniors in room 12 harass me",
			"message": "Warden spoke to the seniors named in your complaint",
		}
	)
	return doc.insert(ignore_permissions=True, ignore_links=True).name


class TestMessageLogAccess(IntegrationTestCase):
	"""Message text is for administrators; confidential grievance traffic is for those who may
	open the grievance."""

	def visible(self, user: str, names: list[str]) -> set[str]:
		condition = permissions.message_log_query_conditions(user) or "1=1"
		return set(
			frappe.db.sql_list(
				f"select name from `tabMessage Log` where name in %(names)s and {condition}",  # noqa: S608
				{"names": names},
			)
		)

	def test_message_text_is_hidden_from_general_readers(self):
		name = grievance_log(None)
		for role in (OFFICE_ROLE, AUDITOR_ROLE, OPERATIONS_ROLE):
			user = make_user(role)
			with self.set_user(user):
				doc = frappe.get_doc("Message Log", name)
				doc.apply_fieldlevel_read_permissions()
			self.assertIsNone(doc.get("subject"), role)
			self.assertIsNone(doc.get("message"), role)
			self.assertEqual(doc.recipient, "98xxxxxx10", role)

		with self.set_user(make_user(ADMIN_ROLE)):
			doc = frappe.get_doc("Message Log", name)
			doc.apply_fieldlevel_read_permissions()
		self.assertIn("Seniors in room 12", doc.subject)
		self.assertIn("Warden spoke", doc.message)

	def test_confidential_grievance_messages_are_hidden_from_other_roles(self):
		assignee = make_user(OFFICE_ROLE)
		confidential = grievance_log(grievance_row(True))
		assigned = grievance_log(grievance_row(True, assigned_to=assignee))
		ordinary = grievance_log(grievance_row(False))
		unrelated = grievance_log(None)
		names = [confidential, assigned, ordinary, unrelated]

		for role in (OFFICE_ROLE, AUDITOR_ROLE, OPERATIONS_ROLE):
			user = make_user(role)
			self.assertEqual(self.visible(user, names), {ordinary, unrelated}, role)
			for name, allowed in ((confidential, False), (ordinary, True), (unrelated, True)):
				doc = frappe.get_doc("Message Log", name)
				self.assertEqual(permissions.message_log_has_permission(doc, "read", user), allowed, role)

		# The staff member it is assigned to, the Warden and administrators see it all.
		self.assertEqual(self.visible(assignee, names), {assigned, ordinary, unrelated})
		self.assertTrue(
			permissions.message_log_has_permission(frappe.get_doc("Message Log", assigned), "read", assignee)
		)
		for user in (make_user(WARDEN_ROLE), make_user(ADMIN_ROLE), "Administrator"):
			self.assertEqual(self.visible(user, names), set(names))
			self.assertTrue(
				permissions.message_log_has_permission(
					frappe.get_doc("Message Log", confidential), "read", user
				)
			)
