"""Health checks, metrics, dead-man heartbeat and metric retention."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime

from hostel360.hostel_operations import health, maintenance, metrics
from hostel360.hostel_operations.settings import get_settings
from hostel360.hostel_operations.tests.helpers import (
	DeliveryRecorder,
	clear_settings_cache,
	configure_settings,
	unique_key,
)


def record_series(samples: list[dict], source: str) -> None:
	for offset in range(0, len(samples), metrics.MAX_BATCH):
		metrics.record_metrics(samples[offset : offset + metrics.MAX_BATCH], source=source)


def fake_check(key, status, core=False, metric=None):
	def check(ctx):
		return health.CheckResult(
			key,
			"Fake component",
			"Availability",
			status,
			f"fake {status}",
			title=f"Fake component is {status}",
			metrics=[(metric, 1.0, "")] if metric else [],
			core=core,
		)

	return check


class TestHealthChecks(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.addClassCleanup(clear_settings_cache)
		super().setUpClass()

	def setUp(self):
		configure_settings()
		self.recorder = DeliveryRecorder().__enter__()
		self.addCleanup(self.recorder.__exit__, None, None, None)

	def ctx(self, now=None):
		return frappe._dict(settings=get_settings(), now=now or now_datetime(), read_only=True)

	def test_each_check_opens_and_resolves_its_own_alert(self):
		key = unique_key("health.fake")
		metric = unique_key("fake_metric")
		with patch.object(health, "CHECKS", (fake_check(key, health.CRIT, metric=metric),)):
			summary = health._run_and_apply()
		alert = frappe.db.get_value(
			"Ops Alert", {"open_key": key}, ["name", "severity", "source"], as_dict=True
		)
		self.assertEqual((alert.severity, alert.source), ("Critical", "Health Check"))
		self.assertEqual(summary["results"][0]["status"], "critical")
		self.assertEqual(frappe.db.count("Ops Metric Sample", {"metric": metric}), 1)
		self.assertTrue(frappe.db.exists("Ops Metric Sample", {"metric": "app_ready"}))

		with patch.object(health, "CHECKS", (fake_check(key, health.OK),)):
			health._run_and_apply()
		self.assertEqual(frappe.db.get_value("Ops Alert", alert.name, "status"), "Resolved")
		self.assertEqual(frappe.db.get_value("Ops Alert", alert.name, "resolved_via"), "Health Check")

		# A check that cannot run neither raises nor resolves anything.
		with patch.object(health, "CHECKS", (fake_check(key, health.UNKNOWN),)):
			health._run_and_apply()
		self.assertFalse(frappe.db.exists("Ops Alert", {"open_key": key}))

	def test_broken_check_does_not_stop_the_others(self):
		key = unique_key("health.fake")

		def broken(ctx):
			raise RuntimeError("boom")

		with patch.object(health, "CHECKS", (broken, fake_check(key, health.WARN))):
			results = health.run_checks(read_only=True)
		self.assertEqual(results[0].status, health.UNKNOWN)
		self.assertEqual(results[1].status, health.WARN)

	def test_heartbeat_only_when_core_checks_pass(self):
		configure_settings(heartbeat_url="https://hc.example.com/ping/hostel360")
		with patch("requests.get", return_value=MagicMock(status_code=200)) as get:
			with patch.object(health, "CHECKS", (fake_check(unique_key(), health.CRIT, core=True),)):
				self.assertIsNone(health._run_and_apply()["heartbeat"])
			get.assert_not_called()

			with patch.object(health, "CHECKS", (fake_check(unique_key(), health.WARN, core=True),)):
				self.assertTrue(health._run_and_apply()["heartbeat"])
		get.assert_called_once()
		self.assertEqual(get.call_args.args[0], "https://hc.example.com/ping/hostel360")
		self.assertEqual(get.call_args.kwargs["timeout"], 5)
		self.assertTrue(frappe.db.get_single_value("Ops Alert Settings", "last_heartbeat_sent_on"))

	def test_real_checks_run(self):
		results = {r.key: r for r in health.run_checks(read_only=True)}
		for key in (
			"health.database.latency",
			"health.redis.cache",
			"health.redis.queue",
			"capacity.disk.sites",
		):
			self.assertIn(key, results)
			self.assertNotEqual(results[key].status, health.UNKNOWN, results[key].message)
		self.assertEqual(results["health.redis.cache"].status, health.OK)

	def test_backup_age_check(self):
		frappe.get_doc(
			{
				"doctype": "Ops Backup Record",
				"backup_type": "Daily",
				"status": "Success",
				"finished_at": add_to_date(now_datetime(), hours=-2),
			}
		).insert()
		stale, _restore = health.check_backups(self.ctx())
		self.assertEqual(stale.status, health.OK)

		later = add_to_date(now_datetime(), hours=30)
		stale, _restore = health.check_backups(self.ctx(later))
		self.assertEqual(stale.status, health.CRIT)

		configure_settings(environment="Development")
		self.assertEqual(health.check_backups(self.ctx()), [])

	def test_error_spike_check(self):
		configure_settings(error_spike_threshold=3)
		for i in range(4):
			frappe.log_error(title=f"Synthetic failure {i}", message="boom")
		result = health.check_error_spike(self.ctx())
		self.assertIn(result.status, (health.WARN, health.CRIT))
		self.assertTrue(result.details["top_errors"])

	def test_failed_login_check(self):
		configure_settings(failed_login_threshold=3)
		for _ in range(4):
			log = frappe.get_doc(
				{
					"doctype": "Activity Log",
					"user": "Administrator",
					"operation": "Login",
					"status": "Failed",
					"subject": "Invalid password",
				}
			).insert(ignore_permissions=True)
			# Activity Log takes the address from the request, which tests do not have.
			frappe.db.set_value("Activity Log", log.name, "ip_address", "203.0.113.7", update_modified=False)
		result = health.check_failed_logins(self.ctx())
		self.assertIn(result.status, (health.WARN, health.CRIT))
		self.assertEqual(result.category, "Security")
		self.assertEqual(result.details["top_ip_addresses"][0]["ip"], "203.0.113.7")

	def test_silent_host_agent(self):
		host = unique_key("host")
		metrics.record_metrics(
			[
				{
					"metric": "agent_heartbeat",
					"value": 1,
					"recorded_at": add_to_date(now_datetime(), minutes=-30),
				}
			],
			source="Host Agent",
			host=host,
		)
		results = {r.key: r for r in health.check_agents(self.ctx())}
		self.assertEqual(results[f"agent.heartbeat.{host}"].status, health.WARN)

	def test_tls_check_uses_cached_certificate_details(self):
		with (
			patch.object(health, "_portal_url", return_value="https://hostel.example.edu"),
			patch.object(
				health, "_inspect_certificate", return_value={"not_after": 0, "not_after_text": "x"}
			) as inspect,
		):
			frappe.cache.delete_value(f"{health.TLS_CACHE_PREFIX}hostel.example.edu:443")
			import time

			inspect.return_value = {"not_after": time.time() + 10 * 86400, "not_after_text": "in ten days"}
			result = health.check_tls(self.ctx())
			self.assertEqual(result.status, health.WARN)
			health.check_tls(self.ctx())
			inspect.assert_called_once()
		frappe.cache.delete_value(f"{health.TLS_CACHE_PREFIX}hostel.example.edu:443")

		with patch.object(health, "_portal_url", return_value="http://hostel.example.edu"):
			self.assertIsNone(health.check_tls(self.ctx()))


class TestMetrics(IntegrationTestCase):
	def test_uptime_from_probe_with_readiness_fallback(self):
		start = add_days(now_datetime(), -3650).replace(second=0, microsecond=0)
		end = add_to_date(start, hours=1)
		self.assertIsNone(metrics.compute_uptime(start, end)["uptime_percent"], "before monitoring began")

		# Readiness samples every five minutes for the first 20 minutes, one of them down.
		metrics.record_metrics(
			[
				{"metric": "app_ready", "value": v, "recorded_at": add_to_date(start, minutes=5 * i)}
				for i, v in enumerate([1, 1, 0, 1])
			],
			source="Health Check",
		)
		fallback = metrics.compute_uptime(start, end)
		self.assertEqual(fallback["source"], "Application readiness checks")
		self.assertEqual(fallback["expected_minutes"], 60)
		self.assertEqual(fallback["measured_downtime_minutes"], 5)
		# Nothing measured the last 40 minutes: they count against availability.
		self.assertEqual(fallback["unmonitored_minutes"], 40)
		self.assertEqual(fallback["downtime_minutes"], 45)
		self.assertEqual(fallback["uptime_percent"], 25.0)

		# Probe samples take precedence over readiness for the minutes they cover.
		metrics.record_metrics(
			[
				{"metric": "uptime_probe", "value": 1, "recorded_at": add_to_date(start, minutes=i)}
				for i in range(60)
			],
			source="Uptime Probe",
		)
		probe = metrics.compute_uptime(start, end)
		self.assertEqual(probe["source"], "External uptime probe")
		self.assertEqual(probe["uptime_percent"], 100.0)
		self.assertEqual(probe["coverage_percent"], 100.0)

	def test_minutes_without_samples_count_as_downtime(self):
		"""A host outage leaves no samples at all; it must not be reported as 100% uptime."""
		start = get_datetime("1999-02-01 00:00:00")
		end = get_datetime("1999-02-02 00:00:00")
		gap = range(600, 780)  # 10:00 to 13:00: the server itself was down
		record_series(
			[
				{"metric": "uptime_probe", "value": 1, "recorded_at": add_to_date(start, minutes=minute)}
				for minute in range(1440)
				if minute not in gap
			],
			source="Uptime Probe",
		)

		uptime = metrics.compute_uptime(start, end)
		self.assertEqual(uptime["unmonitored_minutes"], 180)
		self.assertEqual(uptime["measured_downtime_minutes"], 0)
		self.assertEqual(uptime["downtime_minutes"], 180)
		self.assertEqual(uptime["uptime_percent"], 87.5)
		self.assertLess(uptime["uptime_percent"], 99.9)
		self.assertEqual(uptime["coverage_percent"], 87.5)

		# Readiness checks from inside the application fill the gap where they ran.
		metrics.record_metrics(
			[
				{"metric": "app_ready", "value": 1, "recorded_at": add_to_date(start, minutes=minute)}
				for minute in range(600, 690, 5)
			],
			source="Health Check",
		)
		filled = metrics.compute_uptime(start, end)
		self.assertEqual(filled["unmonitored_minutes"], 90)
		self.assertEqual(filled["source"], "External uptime probe + Application readiness checks")

		days = metrics.daily_uptime("1999-02-01", "1999-02-01")
		self.assertEqual(days[0]["unmonitored_minutes"], 90)

	def test_period_before_monitoring_began_is_not_counted(self):
		start = get_datetime("1998-06-01 00:00:00")
		# Probe installed at noon: the morning is not "unmonitored downtime".
		metrics.record_metrics(
			[
				{"metric": "uptime_probe", "value": 1, "recorded_at": add_to_date(start, minutes=720 + i)}
				for i in range(120)
			],
			source="Uptime Probe",
		)
		uptime = metrics.compute_uptime(start, add_to_date(start, minutes=840))
		self.assertEqual(uptime["expected_minutes"], 120)
		self.assertEqual(uptime["uptime_percent"], 100.0)

	def test_future_samples_are_clamped(self):
		metric = unique_key("clamp")
		metrics.record_metrics(
			[{"metric": metric, "value": 1, "recorded_at": add_days(now_datetime(), 2)}], source="Host Agent"
		)
		recorded = frappe.db.get_value("Ops Metric Sample", {"metric": metric}, "recorded_at")
		self.assertLessEqual(recorded, now_datetime())

	def test_purge_old_metric_samples(self):
		metric = unique_key("purge")
		old = add_days(now_datetime(), -401)
		metrics.record_metrics(
			[{"metric": metric, "value": i, "recorded_at": add_to_date(old, minutes=i)} for i in range(5)]
			+ [{"metric": metric, "value": 99, "recorded_at": add_days(now_datetime(), -10)}],
			source="Health Check",
		)
		deleted = maintenance.purge_old_metric_samples(batch_size=2)
		self.assertGreaterEqual(deleted, 5)
		self.assertEqual(frappe.db.count("Ops Metric Sample", {"metric": metric}), 1)
