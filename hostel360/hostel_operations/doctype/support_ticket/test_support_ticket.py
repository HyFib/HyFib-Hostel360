# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from hostel360.hostel_operations import support
from hostel360.hostel_operations.tests.helpers import (
	LEVEL_ONE_EMAIL,
	LEVEL_THREE_EMAIL,
	DeliveryRecorder,
	clear_settings_cache,
	configure_settings,
)
from hostel360.setup.roles import AUDITOR_ROLE, OPERATIONS_ROLE, STUDENT_ROLE, WARDEN_ROLE

RAISER = "warden.ticket-test@example.com"


def make_ticket(**kwargs):
	return frappe.get_doc(
		{
			"doctype": "Support Ticket",
			"subject": kwargs.pop("subject", f"Fee receipt not downloading {frappe.generate_hash(length=5)}"),
			"category": kwargs.pop("category", "Bug"),
			"priority": kwargs.pop("priority", "Medium"),
			"description": kwargs.pop("description", "<p>Receipt RCPT-2026-00012 shows a blank page.</p>"),
			"contact_phone": "+91 98765 43210",
			**kwargs,
		}
	).insert()


def ensure_user(email: str) -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Ticket", "send_welcome_email": 0, "user_type": "System User"}
		).insert(ignore_permissions=True)
	return email


def ensure_user_with_role(email: str, role: str, user_type: str = "System User") -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Picker",
				"send_welcome_email": 0,
				"user_type": user_type,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)
	return email


class TestSupportTicket(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()
		ensure_user(RAISER)

	def setUp(self):
		configure_settings()
		self.recorder = DeliveryRecorder().__enter__()
		self.addCleanup(self.recorder.__exit__, None, None, None)

	def test_due_dates_and_helpdesk_alert_on_creation(self):
		ticket = make_ticket()
		created = get_datetime(ticket.creation)
		self.assertAlmostEqual((get_datetime(ticket.response_due_by) - created).total_seconds(), 3600, delta=5)
		self.assertAlmostEqual((get_datetime(ticket.resolution_due_by) - created).total_seconds(), 4 * 3600, delta=5)
		self.assertEqual(ticket.contact_phone, "9876543210")
		self.assertEqual(ticket.raised_by, "Administrator")

		ticket.reload()
		self.assertTrue(ticket.ops_alert)
		alert = frappe.get_doc("Ops Alert", ticket.ops_alert)
		self.assertEqual((alert.category, alert.source, alert.status), ("SLA", "Support", "Open"))
		self.assertEqual(alert.alert_key, support.alert_key(ticket.name))
		self.assertIn(LEVEL_ONE_EMAIL, self.recorder.email_recipients(alert.name))

	def test_first_response_when_ticket_leaves_open(self):
		ticket = make_ticket()
		self.assertFalse(ticket.first_response_on)
		ticket.status = "Acknowledged"
		ticket.save()
		self.assertTrue(ticket.first_response_on)
		self.assertEqual(frappe.db.get_value("Ops Alert", ticket.ops_alert, "status"), "Acknowledged")

	def test_operations_comment_counts_as_first_response(self):
		ticket = make_ticket()
		comment = frappe._dict(
			reference_doctype="Support Ticket",
			reference_name=ticket.name,
			comment_type="Comment",
			owner="Administrator",
			creation=now_datetime(),
			content="Looking into it now.",
		)
		support.on_comment(comment)
		self.assertTrue(frappe.db.get_value("Support Ticket", ticket.name, "first_response_on"))

		other = make_ticket()
		support.on_comment(frappe._dict(comment, reference_name=other.name, owner=ensure_user(RAISER)))
		self.assertFalse(frappe.db.get_value("Support Ticket", other.name, "first_response_on"))

	def test_sla_breaches_are_flagged_once_and_alerted(self):
		ticket = make_ticket(priority="High")
		created = get_datetime(ticket.creation)

		support.check_ticket_slas(now=add_to_date(created, minutes=59))
		ticket.reload()
		self.assertFalse(ticket.response_sla_breached)

		events_before = len(self.recorder.events(ticket.ops_alert))
		summary = support.check_ticket_slas(now=add_to_date(created, minutes=61))
		ticket.reload()
		self.assertTrue(ticket.response_sla_breached)
		self.assertFalse(ticket.resolution_sla_breached)
		self.assertGreaterEqual(summary["response_breaches"], 1)
		alert = frappe.get_doc("Ops Alert", ticket.ops_alert)
		self.assertEqual(alert.severity, "Critical", "a breached High ticket becomes critical")
		self.assertIn("SLA breached", alert.title)
		self.assertGreater(len(self.recorder.events(ticket.ops_alert)), events_before)

		summary = support.check_ticket_slas(now=add_to_date(created, minutes=70))
		self.assertEqual(frappe.db.get_value("Ops Alert", ticket.ops_alert, "occurrence_count"), alert.occurrence_count)

		support.check_ticket_slas(now=add_to_date(created, minutes=241))
		ticket.reload()
		self.assertTrue(ticket.resolution_sla_breached)
		self.assertEqual(ticket.escalation_level, 3, "four hours unresolved reaches the Director")

	def test_waiting_on_institution_pauses_the_resolution_clock(self):
		ticket = make_ticket()
		ticket.status = "Acknowledged"
		ticket.save()
		ticket.status = "Waiting on Institution"
		ticket.save()
		self.assertTrue(ticket.waiting_since)

		support.check_ticket_slas(now=add_to_date(get_datetime(ticket.creation), hours=5))
		self.assertFalse(frappe.db.get_value("Support Ticket", ticket.name, "resolution_sla_breached"))

		due_before = get_datetime(ticket.resolution_due_by)
		frappe.db.set_value(
			"Support Ticket", ticket.name, "waiting_since", add_to_date(now_datetime(), hours=-2), update_modified=False
		)
		ticket.reload()
		ticket.status = "In Progress"
		ticket.save()
		extended = (get_datetime(ticket.resolution_due_by) - due_before).total_seconds()
		self.assertAlmostEqual(extended, 2 * 3600, delta=10)
		self.assertFalse(ticket.waiting_since)

	def test_resolution_needs_details_and_resolves_the_alert(self):
		ticket = make_ticket()
		ticket.status = "Resolved"
		self.assertRaises(frappe.ValidationError, ticket.save)

		ticket.reload()
		ticket.status = "Resolved"
		ticket.resolution_details = "<p>Rebuilt the receipt PDF cache.</p>"
		ticket.save()
		self.assertTrue(ticket.resolved_on)
		self.assertTrue(ticket.first_response_on)
		self.assertEqual(frappe.db.get_value("Ops Alert", ticket.ops_alert, "status"), "Resolved")

	def test_outage_ticket_pages_the_director(self):
		ticket = make_ticket(category="Outage", priority="Critical", subject="Portal not opening")
		ticket.reload()
		alert = frappe.get_doc("Ops Alert", ticket.ops_alert)
		self.assertTrue(alert.is_outage)
		self.assertEqual(alert.severity, "Critical")
		self.assertIn(LEVEL_THREE_EMAIL, self.recorder.email_recipients(alert.name))

	def test_raiser_is_told_about_status_changes(self):
		ticket = make_ticket(raised_by=RAISER)
		with patch("hostel360.hostel_communication.dispatch.notify") as notify:
			ticket.status = "In Progress"
			ticket.save()
		notify.assert_called_once()
		args, kwargs = notify.call_args
		self.assertEqual(args[0], "support_ticket_update")
		self.assertEqual(args[2]["status"], "In Progress")
		self.assertEqual(kwargs["reference_name"], ticket.name)

	def test_close_and_reopen(self):
		ticket = make_ticket(raised_by=RAISER)
		ticket.status = "Resolved"
		ticket.resolution_details = "Fixed."
		ticket.save()

		with self.set_user(ensure_user("someone.else@example.com")):
			self.assertRaises(frappe.PermissionError, support.close_ticket, ticket)

		with self.set_user(RAISER):
			support.reopen_ticket(ticket, "Still blank for older receipts")
		ticket.reload()
		self.assertEqual(ticket.status, "Open")
		self.assertFalse(ticket.resolved_on)
		self.assertIn(frappe.db.get_value("Ops Alert", ticket.ops_alert, "status"), ("Open", "Acknowledged"))

		ticket.status = "Resolved"
		ticket.resolution_details = "Fixed the older receipts too."
		ticket.save()
		with self.set_user(RAISER):
			support.close_ticket(ticket)
		self.assertEqual(frappe.db.get_value("Support Ticket", ticket.name, "status"), "Closed")

	def test_only_ticket_assigners_can_list_the_helpdesk_team(self):
		suffix = frappe.generate_hash(length=6)
		helpdesk = ensure_user_with_role(f"ops.picker-{suffix}@example.com", OPERATIONS_ROLE)
		system_manager = ensure_user_with_role(f"sysmgr.picker-{suffix}@example.com", "System Manager")
		warden = ensure_user_with_role(f"warden.picker-{suffix}@example.com", WARDEN_ROLE)
		auditor = ensure_user_with_role(f"auditor.picker-{suffix}@example.com", AUDITOR_ROLE)
		student = ensure_user_with_role(f"student.picker-{suffix}@example.com", STUDENT_ROLE, "Website User")
		search = {
			"doctype": "User",
			"txt": "picker",
			"searchfield": "name",
			"start": 0,
			"page_len": 50,
			"filters": {},
		}

		for outsider in (student, warden, auditor, RAISER):
			with self.set_user(outsider):
				self.assertRaises(frappe.PermissionError, support.operations_user_query, **search)

		for assigner in (helpdesk, system_manager, "Administrator"):
			with self.set_user(assigner):
				found = [row[0] for row in support.operations_user_query(**search)]
			self.assertIn(helpdesk, found)
			self.assertNotIn(warden, found, "only HyFib Operations users are offered")

	def test_sla_compliance(self):
		start = now_datetime()
		good = make_ticket()
		bad = make_ticket()
		frappe.db.set_value("Support Ticket", bad.name, "response_sla_breached", 1)
		result = support.sla_compliance(add_to_date(start, minutes=-1), add_to_date(now_datetime(), minutes=1))
		self.assertGreaterEqual(result["raised"], 2)
		self.assertLess(result["compliance_percent"], 100)
		self.assertTrue(good.name)
