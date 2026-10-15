# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from hostel360.hostel_operations import alerts, channels, escalation, notification
from hostel360.hostel_operations.doctype.ops_alert import ops_alert as ops_alert_module
from hostel360.hostel_operations.doctype.ops_alert.ops_alert import NAME_ALPHABET, NAME_SUFFIX_LENGTH
from hostel360.hostel_operations.settings import get_settings
from hostel360.hostel_operations.tests.helpers import (
	CHANNEL_EMAIL,
	LEVEL_ONE_EMAIL,
	LEVEL_ONE_MOBILE,
	LEVEL_THREE_EMAIL,
	LEVEL_THREE_MOBILE,
	LEVEL_TWO_EMAIL,
	DeliveryRecorder,
	clear_settings_cache,
	configure_settings,
	unique_key,
)


class TestOpsAlert(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()

	def setUp(self):
		configure_settings()
		self.recorder = DeliveryRecorder().__enter__()
		self.addCleanup(self.recorder.__exit__, None, None, None)

	def raise_test_alert(self, severity="Warning", key=None, **kwargs):
		key = key or unique_key()
		name = alerts.raise_alert(
			key, f"Test alert {key}", severity, kwargs.pop("category", "Other"), **kwargs
		)
		self.assertTrue(name, "raise_alert should return the alert name")
		return key, name

	def escalate_at(self, minutes: float):
		return escalation.escalate_open_alerts(get_settings(), add_to_date(now_datetime(), minutes=minutes))

	# Raising and de-duplication --------------------------------------------

	def test_new_alert_notifies_level_one_and_matching_channels(self):
		_key, name = self.raise_test_alert("Warning")
		alert = frappe.get_doc("Ops Alert", name)
		self.assertEqual(alert.status, "Open")
		self.assertEqual(alert.escalation_level, 1)
		self.assertEqual(alert.notified_levels, "1")
		self.assertEqual(alert.last_notified_event, "raised")

		self.assertEqual(self.recorder.events(name), ["raised"])
		recipients = self.recorder.email_recipients(name)
		self.assertIn(LEVEL_ONE_EMAIL, recipients)
		# The e-mail channel only takes critical alerts; level 2 is not due yet.
		self.assertNotIn(CHANNEL_EMAIL, recipients)
		self.assertNotIn(LEVEL_TWO_EMAIL, recipients)
		# Text messages are reserved for critical alerts.
		self.assertFalse(self.recorder.mobiles(name))
		self.assertEqual(len(self.recorder.for_alert(name)["webhooks"]), 1)

	def test_repeat_occurrences_are_counted_on_one_alert(self):
		key, name = self.raise_test_alert("Warning")
		second = alerts.raise_alert(
			key, "Same problem again", "Warning", "Other", description="still failing"
		)
		third = alerts.raise_alert(key, "Same problem again", "Warning", "Other")
		self.assertEqual(second, name)
		self.assertEqual(third, name)

		alert = frappe.get_doc("Ops Alert", name)
		self.assertEqual(alert.occurrence_count, 3)
		self.assertEqual(alert.title, "Same problem again", "the latest wording is kept")
		self.assertEqual(alert.description, "still failing")
		self.assertGreaterEqual(alert.last_seen, alert.first_seen)
		self.assertEqual(frappe.db.count("Ops Alert", {"alert_key": key}), 1)
		self.assertEqual(self.recorder.events(name), ["raised"], "repeats must not page anyone again")

	def test_higher_severity_updates_alert_and_notifies(self):
		key, name = self.raise_test_alert("Warning")
		alerts.raise_alert(key, "Now critical", "Critical", "Other")
		alert = frappe.get_doc("Ops Alert", name)
		self.assertEqual(alert.severity, "Critical")
		self.assertEqual(self.recorder.events(name), ["raised", "severity"])
		self.assertIn(LEVEL_ONE_MOBILE, self.recorder.mobiles(name))
		self.assertIn(CHANNEL_EMAIL, self.recorder.email_recipients(name))

		# A lower severity later never downgrades the alert.
		alerts.raise_alert(key, "Back to warning", "Warning", "Other")
		self.assertEqual(frappe.db.get_value("Ops Alert", name, "severity"), "Critical")

	def test_concurrent_open_falls_back_to_existing_alert(self):
		key, name = self.raise_test_alert("Warning")
		real_get_value = frappe.db.get_value
		state = {"missed": False}

		def miss_first_lookup(*args, **kwargs):
			filters = args[1] if len(args) > 1 else kwargs.get("filters")
			if (
				not state["missed"]
				and args
				and args[0] == "Ops Alert"
				and isinstance(filters, dict)
				and set(filters) == {"open_key"}
			):
				state["missed"] = True
				return None
			return real_get_value(*args, **kwargs)

		with patch.object(frappe.db, "get_value", side_effect=miss_first_lookup):
			again = alerts.raise_alert(key, "Raced", "Warning", "Other")
		self.assertTrue(state["missed"])
		self.assertEqual(again, name)
		self.assertEqual(frappe.db.get_value("Ops Alert", name, "occurrence_count"), 2)
		self.assertEqual(frappe.db.count("Ops Alert", {"alert_key": key}), 1)

	def test_raise_alert_never_raises(self):
		error_logs = frappe.db.count("Error Log")
		with patch.object(alerts, "_record", side_effect=RuntimeError("database on fire")):
			self.assertEqual(alerts.raise_alert(unique_key(), "Broken", "Critical", "Other"), "")
		self.assertEqual(alerts.raise_alert(None, None, "Critical", "Other"), "")
		self.assertGreater(frappe.db.count("Error Log"), error_logs)
		self.assertFalse(alerts.resolve_alert(None))

	def test_deadlock_inside_the_callers_transaction_is_not_hidden(self):
		# Written inline, a deadlock means InnoDB has already rolled back the caller's whole
		# transaction; returning "" would let the caller commit only half of its work.
		with patch.object(alerts, "_record", side_effect=frappe.QueryDeadlockError("1213 deadlock")):
			self.assertRaises(
				frappe.QueryDeadlockError, alerts.raise_alert, unique_key(), "Deadlocked", "Warning", "Other"
			)

	def test_invalid_vocabulary_is_normalised(self):
		_key, name = self.raise_test_alert("Severe", category="Weather", source="Somewhere")
		alert = frappe.get_doc("Ops Alert", name)
		self.assertEqual((alert.severity, alert.category, alert.source), ("Warning", "Other", "App"))

	def test_long_keys_are_shortened_stably(self):
		key = "x" * 300
		first = alerts.raise_alert(key, "Long key", "Info", "Other")
		second = alerts.raise_alert(key, "Long key", "Info", "Other")
		self.assertEqual(first, second)
		self.assertLessEqual(len(frappe.db.get_value("Ops Alert", first, "alert_key")), 140)

	# Naming ----------------------------------------------------------------------

	def test_alert_names_take_no_shared_counter(self):
		def series_rows():
			return frappe.db.sql(
				"select name, current from `tabSeries` where name like %(prefix)s", {"prefix": "ALERT-%"}
			)

		before = series_rows()
		_key, first = self.raise_test_alert()
		_key, second = self.raise_test_alert()
		self.assertEqual(series_rows(), before, "no naming-series row is read or bumped")

		pattern = rf"^ALERT-{now_datetime():%y%m%d}-[{NAME_ALPHABET}]{{{NAME_SUFFIX_LENGTH}}}$"
		self.assertRegex(first, pattern)
		self.assertRegex(second, pattern)
		self.assertNotEqual(first, second)

	def test_a_taken_name_is_never_reused(self):
		_key, taken = self.raise_test_alert()
		fresh = f"ALERT-{now_datetime():%y%m%d}-ZZZZZ"
		self.assertFalse(frappe.db.exists("Ops Alert", fresh))
		with patch.object(ops_alert_module, "make_alert_name", side_effect=[taken, fresh]):
			_key, name = self.raise_test_alert()
		self.assertEqual(name, fresh)

	def test_name_clash_at_insert_is_retried(self):
		# Another process inserts the same name between the existence check and the insert.
		taken_key, taken = self.raise_test_alert()
		real_autoname = ops_alert_module.OpsAlert.autoname
		calls = []

		def clash_once(doc):
			calls.append(doc.alert_key)
			if len(calls) == 1:
				doc.name = taken
				return
			real_autoname(doc)

		messages = len(frappe.local.message_log or [])
		with patch.object(ops_alert_module.OpsAlert, "autoname", clash_once):
			key, name = self.raise_test_alert()
		self.assertEqual(len(calls), 2)
		self.assertNotEqual(name, taken)
		self.assertEqual(frappe.db.get_value("Ops Alert", name, "alert_key"), key)
		self.assertEqual(frappe.db.get_value("Ops Alert", taken, "alert_key"), taken_key)
		self.assertEqual(
			len(frappe.local.message_log or []), messages, "the duplicate-name message is not shown"
		)

	# Resolution ----------------------------------------------------------------

	def test_resolve_sends_recovery_and_releases_key(self):
		key, name = self.raise_test_alert("Warning")
		self.assertTrue(alerts.resolve_alert(key, notes="Disk cleaned", source="Health Check"))

		alert = frappe.get_doc("Ops Alert", name)
		self.assertEqual(alert.status, "Resolved")
		self.assertIsNone(alert.open_key)
		self.assertEqual(alert.resolved_via, "Health Check")
		self.assertEqual(alert.resolution_notes, "Disk cleaned")
		self.assertIsNotNone(alert.duration_minutes)
		self.assertEqual(self.recorder.events(name), ["raised", "resolved"])
		self.assertFalse(alerts.resolve_alert(key), "nothing left to resolve")

		# Once the re-open window has passed the same key opens a fresh alert.
		frappe.db.set_value("Ops Alert", name, "resolved_on", add_to_date(now_datetime(), minutes=-30))
		_same_key, new_name = self.raise_test_alert("Warning", key=key)
		self.assertNotEqual(new_name, name)

	def test_flapping_alert_reopens_quietly(self):
		key, name = self.raise_test_alert("Warning")
		alerts.resolve_alert(key)
		_same, reopened = self.raise_test_alert("Warning", key=key)
		self.assertEqual(reopened, name)

		alert = frappe.get_doc("Ops Alert", name)
		self.assertEqual(alert.status, "Open")
		self.assertEqual(alert.reopened_count, 1)
		self.assertEqual(alert.occurrence_count, 2)
		self.assertIsNone(alert.resolved_on)
		self.assertEqual(self.recorder.events(name), ["raised", "resolved"], "the re-open is held back")

		# Still open once the window has passed: now people are told.
		self.escalate_at(16)
		self.assertEqual(self.recorder.events(name), ["raised", "resolved", "reopened"])

	def test_acknowledge(self):
		_key, name = self.raise_test_alert("Critical")
		alerts.acknowledge_alert(name, "Administrator")
		alert = frappe.get_doc("Ops Alert", name)
		self.assertEqual(alert.status, "Acknowledged")
		self.assertEqual(alert.acknowledged_by, "Administrator")
		self.assertIsNotNone(alert.acknowledged_on)
		# Acknowledgements go to the chat channels only.
		self.assertEqual(self.recorder.events(name), ["raised"])
		self.assertEqual(len(self.recorder.for_alert(name)["webhooks"]), 2)

		alerts.resolve_alert_by_name(name, "fixed", "Administrator")
		self.assertRaises(frappe.ValidationError, alerts.acknowledge_alert, name, "Administrator")

	def test_suppressed_alert_counts_silently(self):
		key, name = self.raise_test_alert("Critical")
		alerts.suppress_alert(name, "Known issue, vendor fixing", "Administrator")
		alerts.raise_alert(key, "Again", "Critical", "Other")
		self.assertEqual(frappe.db.get_value("Ops Alert", name, "occurrence_count"), 2)
		self.escalate_at(200)
		self.assertEqual(self.recorder.events(name), ["raised"])

	def test_notifications_disabled_records_then_catches_up(self):
		configure_settings(enabled=0)
		_key, name = self.raise_test_alert("Warning")
		self.assertEqual(frappe.db.get_value("Ops Alert", name, "escalation_level"), 0)
		self.assertEqual(self.recorder.events(name), [])

		configure_settings(enabled=1)
		self.escalate_at(1)
		self.assertEqual(self.recorder.events(name), ["raised"])

	def test_desk_created_alert_notifies(self):
		alert = frappe.get_doc(
			{
				"doctype": "Ops Alert",
				"alert_key": unique_key("manual"),
				"title": "Planned database maintenance overran",
				"severity": "Warning",
				"category": "Availability",
				"source": "App",
			}
		).insert()
		self.assertEqual(self.recorder.events(alert.name), ["raised"])

		duplicate = frappe.get_doc(
			{
				"doctype": "Ops Alert",
				"alert_key": alert.alert_key,
				"title": "Duplicate",
				"severity": "Warning",
			}
		)
		self.assertRaises(frappe.UniqueValidationError, duplicate.insert)

	# Escalation ----------------------------------------------------------------

	def test_escalation_follows_the_matrix(self):
		_key, name = self.raise_test_alert("Warning")

		self.escalate_at(119)
		self.assertEqual(self.recorder.events(name), ["raised"])

		self.escalate_at(121)
		self.assertEqual(self.recorder.events(name), ["raised", "escalated"])
		last = self.recorder.for_alert(name)["emails"][-1]
		self.assertIn(LEVEL_TWO_EMAIL, last["recipients"])
		self.assertIn(LEVEL_ONE_EMAIL, last["recipients"], "the helpdesk stays informed")
		self.assertNotIn(LEVEL_THREE_EMAIL, last["recipients"])
		self.assertEqual(frappe.db.get_value("Ops Alert", name, "escalation_level"), 2)

		# Running again in the same minute (or the next) pages nobody twice.
		self.escalate_at(121.5)
		self.escalate_at(122)
		self.assertEqual(self.recorder.events(name), ["raised", "escalated"])

		self.escalate_at(181)
		self.assertEqual(self.recorder.events(name), ["raised", "escalated", "escalated"])
		self.assertIn(LEVEL_THREE_EMAIL, self.recorder.for_alert(name)["emails"][-1]["recipients"])
		self.assertEqual(frappe.db.get_value("Ops Alert", name, "notified_levels"), "1,2,3")

		# Warnings get no hourly reminders.
		self.escalate_at(400)
		self.assertEqual(len(self.recorder.events(name)), 3)

	def test_catch_up_notifies_every_skipped_level(self):
		_key, name = self.raise_test_alert("Warning")
		# The escalation job was down for four hours: levels 2 and 3 are told together.
		self.escalate_at(240)
		last = self.recorder.for_alert(name)["emails"][-1]
		self.assertTrue({LEVEL_TWO_EMAIL, LEVEL_THREE_EMAIL} <= set(last["recipients"]))
		self.assertEqual(frappe.db.get_value("Ops Alert", name, "escalation_level"), 3)

	def test_outage_pages_the_director_immediately(self):
		_key, name = self.raise_test_alert("Critical", is_outage=True, category="Availability")
		alert = frappe.get_doc("Ops Alert", name)
		self.assertEqual(alert.last_notified_event, "outage")
		self.assertEqual(alert.escalation_level, 3)
		self.assertIn(LEVEL_THREE_EMAIL, self.recorder.email_recipients(name))
		self.assertIn(LEVEL_THREE_MOBILE, self.recorder.mobiles(name))
		self.assertIn(LEVEL_ONE_MOBILE, self.recorder.mobiles(name))

	def test_existing_alert_becoming_an_outage_escalates_at_once(self):
		key, name = self.raise_test_alert("Critical")
		self.assertNotIn(LEVEL_THREE_EMAIL, self.recorder.email_recipients(name))
		alerts.raise_alert(key, "Portal down", "Critical", "Availability", is_outage=True)
		self.assertIn(LEVEL_THREE_EMAIL, self.recorder.email_recipients(name))
		self.assertTrue(frappe.db.get_value("Ops Alert", name, "is_outage"))

	def test_unacknowledged_critical_alert_is_repeated_hourly(self):
		_key, name = self.raise_test_alert("Critical")
		self.escalate_at(30)
		self.assertEqual(self.recorder.events(name), ["raised"])
		self.escalate_at(61)
		self.assertEqual(self.recorder.events(name), ["raised", "reminder"])
		self.escalate_at(62)
		self.assertEqual(self.recorder.events(name), ["raised", "reminder"])

		alerts.acknowledge_alert(name, "Administrator")
		self.escalate_at(119)
		self.assertEqual(
			self.recorder.events(name), ["raised", "reminder"], "acknowledged alerts are not repeated"
		)
		# Escalation by age still applies to an acknowledged but unresolved alert.
		self.escalate_at(125)
		self.assertEqual(self.recorder.events(name), ["raised", "reminder", "escalated"])

	def test_info_alerts_stay_with_level_one(self):
		_key, name = self.raise_test_alert("Info")
		self.escalate_at(500)
		self.assertEqual(self.recorder.events(name), ["raised"])
		# Info is below the Slack channel's minimum.
		self.assertFalse(self.recorder.for_alert(name)["webhooks"])

	def test_run_escalations_runs_once_per_minute(self):
		moment = add_to_date(
			now_datetime(), days=3650, minutes=int(frappe.generate_hash(length=6), 16) % 500_000
		)
		first = escalation.run_escalations(now=moment)
		second = escalation.run_escalations(now=moment)
		self.assertIsNotNone(first)
		self.assertIsNone(second)

	def test_queued_delivery_reaches_people(self):
		"""Web requests queue the delivery; the queued job must actually be able to run."""
		from frappe.utils.background_jobs import enqueue as real_enqueue

		_key, name = self.raise_test_alert("Critical")
		self.recorder.clear()
		calls = []

		with patch("frappe.enqueue", side_effect=lambda *args, **kwargs: calls.append((args, kwargs))):
			notification._enqueue_delivery(name, "escalated", [2])
		self.assertEqual(self.recorder.events(name), [], "queued, not sent from the request")

		# Later, in a worker: frappe.enqueue keeps its own parameters (queue, event, at_front,
		# ...) and the job receives only the rest.
		args, kwargs = calls[0]
		real_enqueue(*args, now=True, **kwargs)
		self.assertEqual(self.recorder.events(name), ["escalated"])
		self.assertIn(LEVEL_TWO_EMAIL, self.recorder.email_recipients(name))
		self.assertTrue(kwargs["at_front"], "pages must not wait behind bulk student notifications")
		self.assertEqual(kwargs["queue"], notification.alert_queue())

	def test_web_request_alert_is_queued_once_committed(self):
		_key, name = self.raise_test_alert("Warning")
		with (
			patch.object(notification, "_in_web_request", return_value=True),
			patch.object(notification, "_enqueue_delivery") as enqueue,
		):
			notification.dispatch(name, "escalated", [2], committed=True)
			enqueue.assert_called_once_with(name, "escalated", [2])
			enqueue.reset_mock()
			# Not committed yet: wait for the request's commit.
			callbacks = len(frappe.db.after_commit)
			notification.dispatch(name, "escalated", [2])
			enqueue.assert_not_called()
			self.assertEqual(len(frappe.db.after_commit), callbacks + 1)
		frappe.db.after_commit.truncate(callbacks)

	def test_level_without_contacts_is_reported_not_hidden(self):
		settings = frappe.get_doc("Ops Alert Settings")
		for row in settings.escalation_levels:
			if row.level == 2:
				row.notify_emails = ""
				row.notify_mobiles = ""
		settings.flags.ignore_permissions = True
		settings.save()
		clear_settings_cache()

		_key, name = self.raise_test_alert("Warning")
		self.escalate_at(121)
		self.assertEqual(self.recorder.events(name), ["raised", "escalated"])
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Ops Alert", "reference_name": name, "comment_type": "Info"},
			pluck="content",
		)
		self.assertTrue(
			any("Level 2" in (c or "") and "nobody at that level was told" in (c or "") for c in comments),
			comments,
		)
		self.assertTrue(
			frappe.db.exists(
				"Error Log",
				{
					"reference_doctype": "Ops Alert",
					"reference_name": name,
					"method": "Hostel360: alert notification not delivered",
				},
			)
		)

	def test_delivery_is_recorded_on_the_alert(self):
		_key, name = self.raise_test_alert("Warning")
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Ops Alert", "reference_name": name, "comment_type": "Info"},
			pluck="content",
		)
		self.assertTrue(any("delivered" in (c or "") for c in comments))


class TestAlertChannels(IntegrationTestCase):
	def message(self, event="raised", severity="Critical"):
		alert = frappe._dict(
			name="ALERT-261015-K7Q3X",
			alert_key="health.redis.cache",
			title="Redis cache is not responding",
			severity=severity,
			category="Availability",
			source="Health Check",
			status="Open",
			description="Connection refused <script>",
			first_seen=now_datetime(),
			last_seen=now_datetime(),
			occurrence_count=3,
			is_outage=0,
		)
		settings = frappe._dict(
			instance_label="ACGCET Hostel360", environment="Production", escalation_levels=[]
		)
		return notification.build_message(alert, settings, event, [])

	def test_critical_security_alert_states_the_cert_in_deadline(self):
		alert = frappe._dict(
			name="ALERT-261015-M4R8T",
			alert_key="malware-detected-abc",
			title="Malware detected in an uploaded file",
			severity="Critical",
			category="Security",
			source="Security",
			status="Open",
			description="EICAR test signature",
			first_seen=get_datetime("2026-10-15 02:30:00"),
			last_seen=get_datetime("2026-10-15 02:30:00"),
			occurrence_count=1,
			is_outage=0,
		)
		settings = frappe._dict(
			instance_label="ACGCET Hostel360", environment="Production", escalation_levels=[]
		)
		message = notification.build_message(alert, settings, "raised", [])
		self.assertEqual(message["reporting_due_display"], "15-10-2026 08:30")
		self.assertIn("CERT-In", dict(channels.facts(message))["Reporting"])
		self.assertIn("CERT-In report due by 15-10-2026 08:30", channels.short_text(message))
		self.assertIn("CERT-In report due", json.dumps(channels.slack_payload(message)))

		# Not for warnings, other categories, or recovery notices.
		for changes, event in (
			({"severity": "Warning"}, "raised"),
			({"category": "Backup"}, "raised"),
			({}, "resolved"),
			({"alert_key": "security.incident.SINC-2026-00001.reporting"}, "raised"),
		):
			other = frappe._dict(alert, **changes)
			self.assertFalse(notification.build_message(other, settings, event, [])["reporting_due_display"])

	def test_message_text(self):
		message = self.message()
		self.assertEqual(
			channels.subject(message), "[ACGCET Hostel360] CRITICAL: Redis cache is not responding"
		)
		self.assertIn("Seen 3x since", channels.short_text(message))
		self.assertIn("/ops-alert/ALERT-261015-K7Q3X", message["url"])
		html = channels.email_html(message)
		self.assertIn("&lt;script&gt;", html)
		self.assertNotIn("<script>", html)
		self.assertTrue(
			channels.subject(self.message("resolved")).endswith("RESOLVED: Redis cache is not responding")
		)

	def test_chat_payload_shapes(self):
		message = self.message()
		slack = channels.slack_payload(message)
		self.assertEqual(slack["blocks"][0]["type"], "header")
		self.assertEqual(slack["blocks"][-1]["elements"][0]["url"], message["url"])
		self.assertIn("&lt;script&gt;", json.dumps(slack))

		teams = channels.teams_payload(message)
		attachment = teams["attachments"][0]
		self.assertEqual(attachment["contentType"], "application/vnd.microsoft.card.adaptive")
		self.assertEqual(attachment["content"]["type"], "AdaptiveCard")
		self.assertEqual(attachment["content"]["actions"][0]["url"], message["url"])

		chat = channels.google_chat_payload(message)
		self.assertIn("cardsV2", chat)
		self.assertEqual(chat["text"], channels.subject(message))

		generic = channels.generic_payload(message)
		self.assertEqual(generic["event"], "alert.raised")
		self.assertEqual(generic["alert"]["key"], "health.redis.cache")
		self.assertEqual(generic["alert"]["occurrence_count"], 3)

	def test_webhook_post_is_signed_with_short_timeout(self):
		response = MagicMock(status_code=200)
		with patch("requests.post", return_value=response) as post:
			ok, info = channels.post_webhook(
				"Generic Webhook", "https://hooks.example.com/h360", self.message(), signing_secret="s3cret"
			)
		self.assertTrue(ok, info)
		_args, kwargs = post.call_args
		self.assertEqual(kwargs["timeout"], 5)
		self.assertFalse(kwargs["allow_redirects"])
		headers = kwargs["headers"]
		expected = hmac.new(
			b"s3cret", headers["X-H360-Timestamp"].encode() + b"." + kwargs["data"], hashlib.sha256
		).hexdigest()
		self.assertEqual(headers["X-H360-Signature"], expected)
		self.assertEqual(headers["X-H360-Event"], "alert.raised")

	def test_webhook_failures_are_reported_not_raised(self):
		import requests

		self.assertEqual(channels.post_json("http://insecure.example.com", {}), (False, "URL must use https"))
		with patch("requests.post", side_effect=requests.ConnectionError("https://hooks.example.com/SECRET")):
			ok, info = channels.post_json("https://hooks.example.com/SECRET", {})
		self.assertFalse(ok)
		self.assertNotIn("SECRET", info, "webhook URLs carry secrets and must not be logged")
		with patch("requests.post", return_value=MagicMock(status_code=500)):
			self.assertEqual(channels.post_json("https://hooks.example.com/x", {}), (False, "HTTP 500"))
