# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime

from hostel360.hostel_operations import reports
from hostel360.hostel_operations.metrics import record_metrics
from hostel360.hostel_operations.tests.helpers import (
	LEVEL_ONE_EMAIL,
	DeliveryRecorder,
	clear_settings_cache,
	configure_settings,
)

MONTH = "2001-03"
MONTH_START = get_datetime("2001-03-01 00:00:00")
MONTH_MINUTES = 31 * 24 * 60
# Used by tests that change whether a report was sent; MONTH stays untouched for the others.
OTHER_MONTH = "2001-05"
PORTAL_USER = "resident.report-test@example.com"


def at(day: int, hour: int = 10, minute: int = 0):
	return get_datetime(f"2001-03-{day:02d} {hour:02d}:{minute:02d}:00")


def add_alert(severity, category, key, first_seen, occurrences=1, is_outage=0, resolved_after=None):
	doc = frappe.get_doc(
		{
			"doctype": "Ops Alert",
			"alert_key": key,
			"title": f"Synthetic {key}",
			"severity": severity,
			"category": category,
			"source": "Health Check",
			"status": "Resolved" if resolved_after else "Open",
			"first_seen": first_seen,
			"last_seen": first_seen,
			"occurrence_count": occurrences,
			"is_outage": is_outage,
			"resolved_on": add_to_date(first_seen, minutes=resolved_after) if resolved_after else None,
		}
	)
	doc.flags.raised_by_service = True
	return doc.insert()


def add_backup(backup_type, status, finished_at):
	return frappe.get_doc(
		{"doctype": "Ops Backup Record", "backup_type": backup_type, "status": status, "finished_at": finished_at}
	).insert()


class TestMonthlyPerformanceReport(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()
		configure_settings(report_recipients="principal@example.edu\nwarden@example.edu")
		with DeliveryRecorder():
			cls.build_synthetic_month()

	@classmethod
	def build_synthetic_month(cls):
		# Availability: the probe watched the first 100 minutes (50 of them down); readiness
		# checks every five minutes cover the rest of the month except a 30-minute gap
		# (15 March 10:00-10:30) in which nothing was measured at all.
		probe = [
			{"metric": "uptime_probe", "value": 0 if 40 <= i < 90 else 1, "recorded_at": add_to_date(MONTH_START, minutes=i)}
			for i in range(100)
		]
		record_metrics(probe, source="Uptime Probe")
		gap_start = int((at(15, 10) - MONTH_START).total_seconds() // 60)
		readiness = [
			{"metric": "app_ready", "value": 1, "recorded_at": add_to_date(MONTH_START, minutes=minute)}
			for minute in range(100, MONTH_MINUTES, 5)
			if not gap_start <= minute < gap_start + 30
		]
		for offset in range(0, len(readiness), 500):
			record_metrics(readiness[offset : offset + 500], source="Health Check")
		record_metrics(
			[
				{"metric": "cpu_percent", "value": 10, "recorded_at": at(2)},
				{"metric": "cpu_percent", "value": 30, "recorded_at": at(10)},
				{"metric": "cpu_percent", "value": 50, "recorded_at": at(20)},
				{"metric": "memory_percent", "value": 40, "recorded_at": at(2)},
				{"metric": "memory_percent", "value": 60, "recorded_at": at(25)},
				{"metric": "disk_percent", "value": 55, "recorded_at": at(3)},
				{"metric": "disk_percent", "value": 61, "recorded_at": at(30)},
				{"metric": "waf_blocks", "value": 5, "recorded_at": at(4)},
				{"metric": "waf_blocks", "value": 7, "recorded_at": at(5)},
				{"metric": "malware_detections", "value": 1, "recorded_at": at(6)},
				# Outside the month: must be ignored.
				{"metric": "cpu_percent", "value": 99, "recorded_at": get_datetime("2001-04-01 00:00:00")},
				{"metric": "waf_blocks", "value": 1000, "recorded_at": get_datetime("2001-02-28 23:59:00")},
			],
			source="Host Agent",
			host="test-host",
		)

		add_alert("Critical", "Availability", "test.report.portal", at(8), occurrences=3, is_outage=1, resolved_after=30)
		add_alert("Critical", "Security", "malware-detected-report0001", at(9), occurrences=2, resolved_after=5)
		add_alert("Warning", "Security", "security.failed_logins.report", at(11), resolved_after=15)
		# A scanner outage is a security event but not a detection.
		add_alert("Warning", "Security", "malware-scanner-unreachable", at(12), resolved_after=20)
		add_alert("Critical", "Availability", "test.report.april", get_datetime("2001-04-02 10:00:00"), resolved_after=5)

		for day in (1, 2, 3):
			add_backup("Daily", "Success", at(day, 2))
		add_backup("Daily", "Failed", at(4, 2))
		add_backup("Restore Test", "Success", at(20, 15))
		add_backup("Restore Test", "Success", get_datetime("2001-04-02 15:00:00"))

		tickets = []
		for _ in range(2):
			ticket = frappe.get_doc(
				{
					"doctype": "Support Ticket",
					"subject": "Synthetic report ticket",
					"category": "How To",
					"priority": "Low",
					"description": "How do I export the room board?",
				}
			).insert()
			tickets.append(ticket.name)
		frappe.db.set_value("Support Ticket", tickets[0], {"creation": at(12)}, update_modified=False)
		frappe.db.set_value(
			"Support Ticket",
			tickets[1],
			{"creation": at(13), "resolved_on": at(13, 12), "status": "Resolved", "response_sla_breached": 1},
			update_modified=False,
		)

		if not frappe.db.exists("User", PORTAL_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": PORTAL_USER,
					"first_name": "Resident",
					"user_type": "Website User",
					"send_welcome_email": 0,
					"roles": [{"role": "Hostel Student"}],
				}
			).insert(ignore_permissions=True)
		for user in (PORTAL_USER, "Administrator"):
			log = frappe.get_doc(
				{"doctype": "Activity Log", "user": user, "operation": "Login", "status": "Success", "subject": "Login"}
			).insert(ignore_permissions=True)
			frappe.db.set_value("Activity Log", log.name, "creation", at(14), update_modified=False)

	def generate(self, send=False):
		return reports.generate_monthly_performance_report(month=MONTH, send=send)

	def test_figures_for_the_month(self):
		doc = frappe.get_doc("Monthly Performance Report", self.generate())
		self.assertEqual(doc.name, f"MPR-{MONTH}")
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(str(doc.from_date), "2001-03-01")
		self.assertEqual(str(doc.to_date), "2001-03-31")

		# 50 measured minutes down plus 30 unmonitored minutes, out of 44,640.
		self.assertAlmostEqual(doc.uptime_percent, 99.821)
		self.assertEqual(doc.downtime_minutes, 80)
		self.assertEqual(doc.unmonitored_minutes, 30)
		self.assertAlmostEqual(doc.monitoring_coverage_percent, 99.93)
		self.assertEqual(doc.uptime_source, "External uptime probe + Application readiness checks")

		self.assertEqual(doc.incidents, 2)
		self.assertEqual(doc.critical_alerts, 5)
		self.assertEqual(len(doc.incident_details), 2)
		outage = next(row for row in doc.incident_details if row.is_outage)
		self.assertEqual(outage.duration_minutes, 30)

		self.assertAlmostEqual(doc.avg_cpu_percent, 30)
		self.assertAlmostEqual(doc.peak_cpu_percent, 50)
		self.assertAlmostEqual(doc.avg_memory_percent, 50)
		self.assertAlmostEqual(doc.peak_memory_percent, 60)
		self.assertAlmostEqual(doc.disk_used_percent, 61)

		self.assertEqual(doc.security_events, 3)
		self.assertEqual(doc.malware_detections, 3)
		self.assertEqual(doc.waf_blocks, 12)

		self.assertEqual(doc.backups_succeeded, 3)
		self.assertEqual(doc.backups_failed, 1)
		self.assertEqual(str(doc.last_restore_test_on), "2001-03-20 15:00:00")

		self.assertEqual(doc.tickets_raised, 2)
		self.assertEqual(doc.tickets_resolved, 1)
		self.assertAlmostEqual(doc.sla_compliance_percent, 50)

		self.assertEqual(doc.portal_logins, 1)
		expected_students = (
			frappe.db.count("Hostel Student", {"status": "Active"}) if frappe.db.table_exists("Hostel Student") else 0
		)
		self.assertEqual(doc.active_students, expected_students)
		self.assertIn("99.821%", doc.summary)
		self.assertIn("target missed", doc.summary)
		self.assertIn("30 of those minutes had no availability measurement", doc.summary)

	def test_generation_is_idempotent(self):
		first = self.generate()
		second = self.generate()
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("Monthly Performance Report", {"month": MONTH, "docstatus": 1}), 1)

	def test_report_is_emailed_once_with_the_pdf(self):
		with (
			patch.object(reports, "render_pdf", return_value=(b"%PDF-1.4 synthetic", f"MPR-{MONTH}.pdf")),
			patch("frappe.sendmail") as sendmail,
		):
			name = self.generate(send=True)
			self.generate(send=True)
		sendmail.assert_called_once()
		kwargs = sendmail.call_args.kwargs
		self.assertEqual(set(kwargs["recipients"]), {"principal@example.edu", "warden@example.edu", LEVEL_ONE_EMAIL})
		self.assertEqual(kwargs["attachments"][0]["fname"], f"MPR-{MONTH}.pdf")
		self.assertIn("March 2001", kwargs["subject"])
		doc = frappe.get_doc("Monthly Performance Report", name)
		self.assertTrue(doc.sent_on)
		self.assertIn("principal@example.edu", doc.sent_to)

	def test_print_format_renders(self):
		name = self.generate()
		html = frappe.get_print("Monthly Performance Report", name, print_format=reports.PRINT_FORMAT)
		self.assertIn("Monthly Server Performance Report", html)
		self.assertIn("99.821%", html)
		self.assertIn("Monitoring coverage", html)
		self.assertIn("HyFib Solutions Private Limited", html)

	def isolate(self):
		"""Tests in a class share one transaction; undo this test's changes when it ends."""
		savepoint = "mpr_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(savepoint)

		def restore():
			frappe.db.rollback(save_point=savepoint)
			clear_settings_cache()

		self.addCleanup(restore)

	def clear_institution_contacts(self):
		configure_settings(report_recipients="")
		frappe.db.set_single_value("Hostel Settings", "office_email", "")

	def test_report_is_not_marked_sent_without_institution_recipients(self):
		"""A copy to the HyFib helpdesk alone is not delivery to the institution (TR 13)."""
		self.isolate()
		self.clear_institution_contacts()
		with (
			DeliveryRecorder(),
			patch.object(reports, "render_pdf", return_value=(b"%PDF-1.4 synthetic", "report.pdf")),
			patch("frappe.sendmail") as sendmail,
		):
			name = reports.generate_monthly_performance_report(month=OTHER_MONTH, send=True)
			sendmail.assert_not_called()
			self.assertFalse(frappe.db.get_value("Monthly Performance Report", name, "sent_on"))
			self.assertEqual(reports.report_recipients(), [])
			alert = frappe.db.get_value("Ops Alert", {"open_key": reports.NO_RECIPIENTS_ALERT_KEY}, "name")
			self.assertTrue(alert, "operations must be told the report is stuck")

			# The hostel office address is the institution's fallback contact.
			frappe.db.set_single_value("Hostel Settings", "office_email", "office@example.edu")
			reports.generate_monthly_performance_report(month=OTHER_MONTH, send=True)
		sendmail.assert_called_once()
		self.assertEqual(set(sendmail.call_args.kwargs["recipients"]), {"office@example.edu", LEVEL_ONE_EMAIL})
		self.assertTrue(frappe.db.get_value("Monthly Performance Report", name, "sent_on"))
		self.assertEqual(frappe.db.get_value("Ops Alert", alert, "status"), "Resolved")

	def test_catch_up_sends_a_report_held_back_on_the_first(self):
		self.isolate()
		self.clear_institution_contacts()
		moment = get_datetime("2001-06-03 09:00:00")
		with (
			DeliveryRecorder(),
			patch.object(reports, "render_pdf", return_value=(b"%PDF", "report.pdf")),
			patch("frappe.sendmail") as sendmail,
		):
			name = reports.generate_monthly_performance_report(month=OTHER_MONTH, send=True)
			status = reports.delivery_status(moment)
			self.assertEqual((status["month"], status["name"], status["sent_on"]), (OTHER_MONTH, name, None))
			self.assertTrue(status["applicable"])
			# Still nobody to send it to: retried, but not sent and not marked sent.
			reports.catch_up_monthly_report(moment)
			sendmail.assert_not_called()
			self.assertFalse(frappe.db.get_value("Monthly Performance Report", name, "sent_on"))

			configure_settings(report_recipients="principal@example.edu")
			self.assertEqual(reports.catch_up_monthly_report(moment), name)
			self.assertIsNone(reports.catch_up_monthly_report(moment), "never e-mailed twice")
		sendmail.assert_called_once()
		self.assertFalse(reports.delivery_status(get_datetime("2001-06-01 06:00:00"))["applicable"], "before the 07:00 run")

	def test_health_check_retries_and_escalates_an_unsent_report(self):
		from hostel360.hostel_operations import health
		from hostel360.hostel_operations.settings import get_settings

		self.isolate()
		self.clear_institution_contacts()
		with DeliveryRecorder(), patch.object(reports, "render_pdf", return_value=(b"%PDF", "report.pdf")), patch("frappe.sendmail"):
			reports.generate_monthly_performance_report(month=OTHER_MONTH, send=True)

			def ctx(moment, read_only=True):
				return frappe._dict(settings=get_settings(), now=get_datetime(moment), read_only=read_only)

			self.assertEqual(health.check_monthly_report(ctx("2001-06-02 09:00:00")).status, health.WARN)
			late = health.check_monthly_report(ctx("2001-06-04 09:00:00"))
			self.assertEqual(late.status, health.CRIT)
			self.assertIn(OTHER_MONTH, late.title)

			configure_settings(report_recipients="principal@example.edu")
			frappe.cache.delete(frappe.cache.make_key(health.REPORT_CATCH_UP_KEY))
			fixed = health.check_monthly_report(ctx("2001-06-04 10:00:00", read_only=False))
		self.assertEqual(fixed.status, health.OK)
		self.assertTrue(frappe.db.get_value("Monthly Performance Report", f"MPR-{OTHER_MONTH}", "sent_on"))

	def test_month_helpers(self):
		self.assertEqual(reports.previous_month("2026-10-15"), "2026-09")
		self.assertEqual(reports.previous_month("2026-01-01"), "2025-12")
		self.assertRaises(frappe.ValidationError, reports.month_bounds, "2026-13")
		self.assertRaises(frappe.ValidationError, reports.month_bounds, "Sept 2026")
