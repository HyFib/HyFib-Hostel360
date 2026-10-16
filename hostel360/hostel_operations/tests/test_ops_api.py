"""Host agent ingest, OCI notifications, alert lifecycle API and health endpoints."""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request

from hostel360.api import health as health_api
from hostel360.api import ops
from hostel360.hostel_operations.tests.helpers import (
	TEST_INGEST_SECRET,
	TEST_OCI_TOKEN,
	DeliveryRecorder,
	clear_settings_cache,
	configure_settings,
	unique_key,
)


def signed_headers(body: bytes, timestamp: int | None = None, secret: str = TEST_INGEST_SECRET) -> dict:
	timestamp = int(time.time()) if timestamp is None else timestamp
	signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
	return {"X-H360-Timestamp": str(timestamp), "X-H360-Signature": signature}


class OpsRequestMixin:
	def post(self, method: str, body: bytes, headers: dict | None = None, query_string: str = ""):
		set_request(
			method="POST",
			path=f"/api/method/{method}",
			data=body,
			headers=headers or {},
			content_type="application/json",
			query_string=query_string,
		)
		frappe.local.request_ip = "127.0.0.1"
		frappe.local.form_dict = frappe._dict(cmd=method)

	def ingest(self, payload: dict, headers: dict | None = None, body: bytes | None = None):
		body = body if body is not None else json.dumps(payload).encode()
		self.post("hostel360.api.ops.ingest", body, headers if headers is not None else signed_headers(body))
		return ops.ingest()


class TestOpsIngest(OpsRequestMixin, IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()

	def setUp(self):
		configure_settings()
		self.recorder = DeliveryRecorder().__enter__()
		self.addCleanup(self.recorder.__exit__, None, None, None)
		self.addCleanup(_reset_request)

	def test_valid_alert_is_recorded(self):
		key = unique_key("agent.clamav")
		result = self.ingest(
			{
				"type": "alert",
				"key": key,
				"title": "ClamAV daemon stopped",
				"severity": "Critical",
				"category": "Security",
				"description": "clamd is not running",
				"details": {"unit": "clamav-daemon.service"},
			}
		)
		self.assertTrue(result["ok"])
		alert = frappe.get_doc("Ops Alert", result["alert"])
		self.assertEqual((alert.alert_key, alert.source, alert.severity), (key, "Host Agent", "Critical"))

		resolved = self.ingest({"type": "resolve", "key": key, "notes": "clamd restarted"})
		self.assertTrue(resolved["resolved"])
		self.assertEqual(frappe.db.get_value("Ops Alert", alert.name, "resolved_via"), "Host Agent")

	def test_invalid_signature_is_rejected(self):
		body = json.dumps({"type": "heartbeat", "host": "h360-prod"}).encode()
		headers = signed_headers(body, secret="not-the-secret")
		self.assertRaises(frappe.AuthenticationError, self.ingest, {}, headers, body)

		tampered = signed_headers(body)
		self.assertRaises(frappe.AuthenticationError, self.ingest, {}, tampered, body + b" ")
		self.assertRaises(frappe.AuthenticationError, self.ingest, {}, {"X-H360-Timestamp": "abc"}, body)

	def test_expired_or_future_timestamp_is_rejected(self):
		body = json.dumps({"type": "heartbeat", "host": "h360-prod"}).encode()
		for skew in (-301, 301):
			headers = signed_headers(body, timestamp=int(time.time()) + skew)
			self.assertRaises(frappe.AuthenticationError, self.ingest, {}, headers, body)
		headers = signed_headers(body, timestamp=int(time.time()) - 250)
		self.assertTrue(self.ingest({}, headers, body)["ok"])

	def test_replayed_request_is_rejected(self):
		body = json.dumps({"type": "heartbeat", "host": unique_key("host")}).encode()
		headers = signed_headers(body)
		self.assertTrue(self.ingest({}, headers, body)["ok"])
		self.assertRaises(ops.ReplayedRequestError, self.ingest, {}, headers, body)

	def test_signature_prefix_is_accepted(self):
		body = json.dumps({"type": "heartbeat", "host": unique_key("host")}).encode()
		headers = signed_headers(body)
		headers["X-H360-Signature"] = "sha256=" + headers["X-H360-Signature"].upper()
		self.assertTrue(self.ingest({}, headers, body)["ok"])

	def test_metrics_batch(self):
		host = unique_key("host")
		result = self.ingest(
			{
				"type": "metrics",
				"host": host,
				"samples": [
					{"metric": "cpu_percent", "value": 37.5, "unit": "%"},
					{"metric": "memory_percent", "value": "61.2", "unit": "%"},
					{"metric": "disk_percent", "value": "not a number"},
					{"value": 1},
				],
			}
		)
		self.assertEqual(result["recorded"], 2)
		self.assertEqual(result["rejected"], 2)
		self.assertEqual(frappe.db.count("Ops Metric Sample", {"host": host}), 2)
		self.assertEqual(
			frappe.db.get_value("Ops Metric Sample", {"host": host, "metric": "cpu_percent"}, "source"),
			"Host Agent",
		)
		self.assertRaises(frappe.ValidationError, self.ingest, {"type": "metrics", "samples": []})

	def test_backup_failure_raises_critical_alert(self):
		result = self.ingest(
			{
				"type": "backup",
				"backup_type": "Daily",
				"status": "Failed",
				"host": "h360-prod",
				"message": "restic: unable to open repository",
				"size_bytes": 0,
			}
		)
		record = frappe.get_doc("Ops Backup Record", result["record"])
		self.assertEqual(record.status, "Failed")
		alert = frappe.db.get_value(
			"Ops Alert", {"open_key": "backup.daily.failed"}, ["severity", "category"], as_dict=True
		)
		self.assertEqual((alert.severity, alert.category), ("Critical", "Backup"))

		self.ingest({"type": "backup", "backup_type": "Daily", "status": "Success", "size_bytes": 1024})
		self.assertFalse(frappe.db.exists("Ops Alert", {"open_key": "backup.daily.failed"}))
		self.assertRaises(
			frappe.ValidationError,
			self.ingest,
			{"type": "backup", "backup_type": "Hourly", "status": "Success"},
		)

	def test_heartbeat_records_and_resolves_silence_alert(self):
		host = unique_key("host")
		from hostel360.hostel_operations.alerts import raise_alert

		alert = raise_alert(f"agent.heartbeat.{host}", "Agent silent", "Warning", "Availability")
		self.assertTrue(self.ingest({"type": "heartbeat", "host": host})["ok"])
		self.assertEqual(frappe.db.count("Ops Metric Sample", {"host": host, "metric": "agent_heartbeat"}), 1)
		self.assertEqual(frappe.db.get_value("Ops Alert", alert, "status"), "Resolved")

	def test_unknown_type_and_bad_json(self):
		self.assertRaises(frappe.ValidationError, self.ingest, {"type": "reboot"})
		body = b"not json"
		self.assertRaises(frappe.ValidationError, self.ingest, {}, signed_headers(body), body)

	def test_oversized_body_is_rejected(self):
		body = b"{" + b" " * (ops.MAX_BODY_BYTES + 10) + b"}"
		self.assertRaises(ops.PayloadTooLargeError, self.ingest, {}, signed_headers(body), body)

	def as_request(self, call, *args):
		"""Run ``call`` the way Frappe runs a request: an exception rolls back its writes."""
		savepoint = "ingest_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(savepoint)
		try:
			return call(*args)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			raise

	def test_transient_failure_is_retryable_and_not_mistaken_for_a_replay(self):
		key = unique_key("agent.backup")
		body = json.dumps(
			{"type": "alert", "key": key, "title": "Backup failed", "severity": "Critical"}
		).encode()
		headers = signed_headers(body)

		with patch("hostel360.hostel_operations.alerts.raise_alert", return_value=""):
			with self.assertRaises(ops.IngestUnavailableError) as failure:
				self.as_request(self.ingest, {}, headers, body)
		# 503 tells the agent to keep the message and try again (417 made it drop it).
		self.assertEqual(failure.exception.http_status_code, 503)
		self.assertFalse(frappe.db.exists("Ops Ingest Receipt", "sig:" + headers["X-H360-Signature"]))

		# The agent retries with the same signature; the failed attempt left nothing behind.
		result = self.as_request(self.ingest, {}, headers, body)
		self.assertTrue(frappe.db.exists("Ops Alert", result["alert"]))
		# Only now is a repeat a replay of a committed request.
		self.assertRaises(ops.ReplayedRequestError, self.ingest, {}, headers, body)

	def test_database_lock_trouble_answers_503(self):
		with patch.object(ops, "_ingest_backup", side_effect=frappe.QueryTimeoutError("lock wait")):
			with self.assertRaises(ops.IngestUnavailableError):
				self.ingest({"type": "backup", "backup_type": "Daily", "status": "Success"})

	def test_payload_errors_are_permanent(self):
		for payload in (
			{"type": "reboot"},
			{"type": "alert"},
			{"type": "metrics", "samples": []},
			{"type": "backup", "backup_type": "Hourly", "status": "Success"},
			{"type": "backup", "backup_type": "Daily", "status": "Success", "size_bytes": "lots"},
			{"type": "heartbeat", "message_id": "x"},
		):
			# A distinct body per request: an identical signed request would be a replay.
			payload = {**payload, "nonce": frappe.generate_hash(length=10)}
			with self.assertRaises(ops.IngestPayloadError, msg=str(payload)) as raised:
				self.ingest(payload)
			self.assertEqual(raised.exception.http_status_code, 422)

	def test_message_id_recognises_a_resigned_retry(self):
		host = unique_key("host")
		payload = {"type": "heartbeat", "host": host, "message_id": "msg-" + frappe.generate_hash(length=20)}
		body = json.dumps(payload).encode()
		self.assertTrue(self.ingest({}, signed_headers(body, timestamp=int(time.time()) - 60), body)["ok"])
		# Same message re-signed later (from the agent's spool): delivered already.
		self.assertRaises(ops.ReplayedRequestError, self.ingest, {}, signed_headers(body), body)
		self.assertEqual(frappe.db.count("Ops Metric Sample", {"host": host, "metric": "agent_heartbeat"}), 1)

	def test_invalid_signature_alert_is_retried_when_it_could_not_be_raised(self):
		body = json.dumps({"type": "heartbeat", "host": "h360-prod"}).encode()
		bad = signed_headers(body, secret="not-the-secret")
		bucket = int(frappe.generate_hash(length=8), 16)
		with (
			patch.object(ops, "_invalid_signature_bucket", return_value=bucket),
			patch(
				"hostel360.hostel_operations.alerts.raise_alert", side_effect=["", "ALERT-X"]
			) as raise_alert,
		):
			for _ in range(ops.INVALID_SIGNATURE_ALERT_THRESHOLD + 3):
				self.assertRaises(frappe.AuthenticationError, self.ingest, {}, bad, body)
		# The 20th request failed to record it, the 21st succeeded, and then it stopped.
		self.assertEqual(raise_alert.call_count, 2)


class TestOciNotifications(OpsRequestMixin, IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()

	def setUp(self):
		configure_settings()
		self.recorder = DeliveryRecorder().__enter__()
		self.addCleanup(self.recorder.__exit__, None, None, None)
		self.addCleanup(_reset_request)

	def notify(
		self, payload: dict, token: str = TEST_OCI_TOKEN, headers: dict | None = None, extra_query: str = ""
	):
		self.post(
			"hostel360.api.ops.oci_notification",
			json.dumps(payload).encode(),
			headers=headers,
			query_string=f"token={token}{extra_query}",
		)
		return ops.oci_notification()

	def alarm(self, status: str, alarm_id: str, **meta):
		return {
			"dedupeKey": f"dedupe-{alarm_id}",
			"title": "Hostel360 VM CPU above 90%",
			"body": "CPU utilisation has been above 90% for 5 minutes.",
			"type": "OK_TO_FIRING" if status == "FIRING" else "FIRING_TO_OK",
			"severity": "CRITICAL",
			"timestampEpochMillis": 1792051200000,
			"alarmMetaData": [
				{
					"id": alarm_id,
					"status": status,
					"severity": meta.get("severity", "CRITICAL"),
					"namespace": meta.get("namespace", "oci_computeagent"),
					"query": "CpuUtilization[1m].mean() > 90",
					"totalMetricsFiring": 1,
					"dimensions": [{"resourceDisplayName": "h360-prod"}],
					"alarmUrl": "https://cloud.oracle.com/monitoring/alarms/ocid1.alarm.oc1.ap-hyderabad-1.example",
				}
			],
			"version": 1.5,
		}

	def test_bad_token_is_rejected(self):
		self.assertRaises(frappe.AuthenticationError, self.notify, {}, token="wrong")
		# A token in the body must not be accepted in place of the query string.
		self.post(
			"hostel360.api.ops.oci_notification",
			json.dumps({"token": TEST_OCI_TOKEN}).encode(),
			query_string="",
		)
		self.assertRaises(frappe.AuthenticationError, ops.oci_notification)

	def test_subscription_confirmation(self):
		url = "https://cell1.notification.ap-hyderabad-1.oci.oraclecloud.com/20181201/subscriptions/ocid1.onssubscription.oc1/confirmation?token=abc"
		with patch("requests.get", return_value=MagicMock(status_code=200)) as get:
			result = self.notify({}, headers={"X-OCI-NS-ConfirmationURL": url})
		self.assertTrue(result["confirmed"])
		get.assert_called_once()
		self.assertEqual(get.call_args.args[0], url)
		self.assertEqual(get.call_args.kwargs["timeout"], 5)

		with patch("requests.get", return_value=MagicMock(status_code=200)) as get:
			result = self.notify({"ConfirmationURL": url})
		self.assertTrue(result["confirmed"])

	def test_confirmation_only_calls_oracle(self):
		with patch("requests.get") as get:
			for url in (
				"https://evil.example.com/confirm",
				"http://cell1.notification.ap-hyderabad-1.oci.oraclecloud.com/confirm",
				"https://oraclecloud.com.evil.example/confirm",
			):
				self.assertRaises(frappe.ValidationError, self.notify, {"ConfirmationURL": url})
		get.assert_not_called()

	def test_firing_and_ok_map_to_alert_lifecycle(self):
		alarm_id = f"ocid1.alarm.oc1.ap-hyderabad-1.{frappe.generate_hash(length=12)}"
		result = self.notify(self.alarm("FIRING", alarm_id))
		action = result["actions"][0]
		self.assertEqual(action["action"], "raised")
		alert = frappe.get_doc("Ops Alert", action["alert"])
		self.assertEqual(
			(alert.source, alert.severity, alert.category, alert.status),
			("OCI Monitoring", "Critical", "Capacity", "Open"),
		)
		self.assertFalse(alert.is_outage)
		self.assertIn(alarm_id, alert.details)

		# Repeats of the same alarm stay on the same alert.
		again = self.notify(self.alarm("FIRING", alarm_id))
		self.assertEqual(again["actions"][0]["alert"], alert.name)

		cleared = self.notify(self.alarm("OK", alarm_id))
		self.assertEqual(cleared["actions"][0]["action"], "resolved")
		self.assertEqual(frappe.db.get_value("Ops Alert", alert.name, "status"), "Resolved")
		self.assertEqual(frappe.db.get_value("Ops Alert", alert.name, "resolved_via"), "OCI Monitoring")

	def test_alarm_that_cannot_be_recorded_is_answered_503(self):
		from hostel360.hostel_operations import alerts

		alarm_id = f"ocid1.alarm.oc1.ap-hyderabad-1.{frappe.generate_hash(length=12)}"
		with patch.object(alerts, "_raise_alert", side_effect=frappe.QueryTimeoutError("lock wait timeout")):
			with self.assertRaises(ops.AlarmNotRecordedError) as failure:
				self.notify(self.alarm("FIRING", alarm_id, namespace="oci_lbaas"))
		self.assertEqual(failure.exception.http_status_code, 503, "OCI redelivers only after a 5xx")

		# Redelivered later: recorded normally.
		raised = self.notify(self.alarm("FIRING", alarm_id, namespace="oci_lbaas"))["actions"][0]
		self.assertEqual(raised["action"], "raised")

		# An OK that cannot be applied must not be acknowledged either, or the alert pages forever.
		with patch.object(
			alerts, "_resolve_alert", side_effect=frappe.QueryTimeoutError("lock wait timeout")
		):
			self.assertRaises(ops.AlarmNotRecordedError, self.notify, self.alarm("OK", alarm_id))
		self.assertEqual(frappe.db.get_value("Ops Alert", raised["alert"], "status"), "Open")
		self.assertEqual(self.notify(self.alarm("OK", alarm_id))["actions"][0]["action"], "resolved")
		# An OK for an alarm with nothing open is fine.
		self.assertEqual(self.notify(self.alarm("OK", alarm_id))["actions"][0]["action"], "resolved")

	def test_load_balancer_alarm_is_an_outage(self):
		alarm_id = f"ocid1.alarm.oc1.ap-hyderabad-1.{frappe.generate_hash(length=12)}"
		result = self.notify(self.alarm("FIRING", alarm_id, namespace="oci_lbaas"))
		alert = frappe.get_doc("Ops Alert", result["actions"][0]["alert"])
		self.assertTrue(alert.is_outage)
		self.assertEqual(alert.category, "Availability")
		self.assertEqual(alert.escalation_level, 3)

		warning_id = f"ocid1.alarm.oc1.ap-hyderabad-1.{frappe.generate_hash(length=12)}"
		result = self.notify(self.alarm("FIRING", warning_id, severity="WARNING"), extra_query="&outage=1")
		self.assertTrue(frappe.db.get_value("Ops Alert", result["actions"][0]["alert"], "is_outage"))


class TestAlertActionsAndHealthEndpoints(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()
		cls.plain_user = _user("ops-api-plain@example.com", [])
		cls.ops_user = _user("ops-api-hyfib@example.com", ["HyFib Operations"])
		cls.auditor = _user("ops-api-auditor@example.com", ["Institution Auditor"])

	def setUp(self):
		configure_settings()
		self.recorder = DeliveryRecorder().__enter__()
		self.addCleanup(self.recorder.__exit__, None, None, None)

	def test_lifecycle_endpoints_require_operations_role(self):
		from hostel360.hostel_operations.alerts import raise_alert

		name = raise_alert(unique_key("api"), "API lifecycle", "Critical", "Other")
		with self.set_user(self.plain_user):
			self.assertRaises(frappe.PermissionError, ops.acknowledge, name)
			self.assertRaises(frappe.PermissionError, ops.resolve, name, "nope")
		with self.set_user(self.auditor):
			self.assertRaises(frappe.PermissionError, ops.suppress, name, "nope")

		with self.set_user(self.ops_user):
			self.assertEqual(ops.acknowledge(name)["status"], "Acknowledged")
			self.assertEqual(ops.resolve(name, "Restarted Redis")["status"], "Resolved")
		alert = frappe.get_doc("Ops Alert", name)
		self.assertEqual(alert.acknowledged_by, self.ops_user)
		self.assertEqual(alert.resolved_by, self.ops_user)

	def test_liveness_and_readiness(self):
		self.assertEqual(health_api.liveness(), {"status": "ok"})
		self.assertEqual(health_api.readiness(), {"status": "ok"})

		frappe.local.response = frappe._dict()
		self.addCleanup(setattr, frappe.local, "response", frappe._dict())
		with patch("frappe.utils.background_jobs.get_redis_conn", side_effect=ConnectionError("down")):
			self.assertEqual(health_api.readiness(), {"status": "degraded"})
		self.assertEqual(frappe.local.response.http_status_code, 503)

	def test_status_is_for_operations_and_auditors(self):
		with self.set_user(self.plain_user):
			self.assertRaises(frappe.PermissionError, health_api.status)
		with self.set_user(self.auditor):
			status = health_api.status()
		self.assertIn(status["status"], ("ok", "warning", "critical"))
		keys = {component["key"] for component in status["components"]}
		self.assertTrue({"health.database.latency", "health.redis.cache", "capacity.disk.sites"} <= keys)
		self.assertIn("Critical", status["open_alerts"])
		self.assertIn("hostel360", status["versions"])


def _user(email: str, roles: list[str]) -> str:
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Ops",
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		)
		for role in roles:
			user.append("roles", {"role": role})
		user.insert(ignore_permissions=True)
	return email


def _reset_request():
	frappe.local.request = None
	frappe.local.form_dict = frappe._dict()
