"""Checks for deploy/monitoring/uptime-kuma/relay/h360-kuma-relay.py (run by checks.sh).

	python3 -I deploy/tests/host/test_kuma_relay.py
"""

import hashlib
import hmac
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error

RELAY_PATH = os.path.join(
	os.path.dirname(os.path.abspath(__file__)), "..", "..", "monitoring", "uptime-kuma", "relay", "h360-kuma-relay.py"
)
spec = importlib.util.spec_from_file_location("h360_kuma_relay", RELAY_PATH)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

METRICS = """# HELP monitor_status Monitor Status (1 = UP, 0= DOWN, 2= PENDING, 3= MAINTENANCE)
# TYPE monitor_status gauge
monitor_status{monitor_name="Portal login page",monitor_type="http",monitor_url="https://hostel.test/login",monitor_hostname="null",monitor_port="null"} 0
monitor_status{monitor_type="keyword",monitor_name="Portal readiness",monitor_url="https://hostel.test/api/method/hostel360.api.health.readiness",monitor_hostname="null",monitor_port="null"} %s
monitor_status{monitor_name="Say \\"hi\\"",monitor_type="http"} 1
# HELP monitor_response_time Monitor Response Time (ms)
monitor_response_time{monitor_name="Portal readiness",monitor_type="keyword"} 182.4
monitor_response_time{monitor_name="Portal login page",monitor_type="http"} -1
"""


class FakeResponse:
	def __init__(self, status):
		self.status = status

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False


class FakeOpener:
	"""Answers ingest requests with the given statuses in turn and keeps what was sent."""

	def __init__(self, *statuses):
		self.statuses = list(statuses)
		self.sent = []

	def open(self, request, timeout=None):
		self.sent.append(request)
		status = self.statuses.pop(0) if self.statuses else 200
		if status == "unreachable":
			raise urllib.error.URLError("connection refused")
		if 200 <= status < 300:
			return FakeResponse(status)
		raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(b""))


class RelayTestCase(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		secret_file = os.path.join(self.tmp.name, "secret")
		with open(secret_file, "w") as handle:
			handle.write("s3cret\n")
		self.settings = relay.Settings(
			{
				"H360_INGEST_URL": "https://hostel.test/api/method/hostel360.api.ops.ingest",
				"H360_INGEST_SECRET_FILE": secret_file,
				"KUMA_API_KEY_FILE": os.path.join(self.tmp.name, "missing"),
				"RELAY_SPOOL_DIR": os.path.join(self.tmp.name, "spool"),
				"RELAY_BATCH": "3",
			}
		)

	def tearDown(self):
		self.tmp.cleanup()

	def spooled(self):
		return relay.spooled(self.settings)

	def relay_with(self, statuses, opener=None):
		pages = iter(statuses)
		instance = relay.Relay(self.settings, opener=opener or FakeOpener())
		relay.read_kuma = lambda settings: METRICS % next(pages)
		return instance


class TestParsing(RelayTestCase):
	def test_status_and_response_time_of_the_named_monitor(self):
		self.assertEqual(relay.parse_metrics(METRICS % 1, "Portal readiness"), (1, 182.4))
		self.assertEqual(relay.parse_metrics(METRICS % 0, "Portal login page"), (0, None))

	def test_escaped_label_values(self):
		self.assertEqual(relay.parse_metrics(METRICS % 1, 'Say "hi"')[0], 1)

	def test_monitor_not_on_the_page(self):
		self.assertEqual(relay.parse_metrics(METRICS % 1, "Nope"), (None, None))

	def test_status_mapping(self):
		at = "2026-10-15T06:00:15+00:00"
		self.assertEqual([s["value"] for s in relay.samples_for(1, 120.6, at)], [1, 121])
		self.assertEqual(relay.samples_for(0, None, at), [{"metric": "uptime_external", "value": 0, "recorded_at": at}])
		self.assertEqual(relay.samples_for(2, 50, at)[0]["value"], 0, "pending is a failed check")
		self.assertEqual(relay.samples_for(3, None, at), [], "maintenance leaves the minute to the on-host probe")


class TestDelivery(RelayTestCase):
	def test_batches_of_the_configured_size(self):
		instance = self.relay_with([1, 1, 1, 1])
		for _ in range(3):
			instance.reading()
		self.assertEqual(len(self.spooled()), 1)
		with open(self.spooled()[0]) as handle:
			message = json.load(handle)
		self.assertEqual((message["type"], message["source"], message["host"]), ("metrics", "Uptime Probe", "uptime-kuma-mumbai"))
		self.assertEqual([s["metric"] for s in message["samples"]].count("uptime_external"), 3)
		self.assertRegex(message["message_id"], r"^[0-9a-f-]{36}$")

	def test_a_change_of_status_is_sent_at_once(self):
		instance = self.relay_with([1, 0])
		instance.reading()
		self.assertEqual(self.spooled(), [])
		instance.reading()
		self.assertEqual(len(self.spooled()), 1)

	def test_signed_like_the_host_agent(self):
		instance = self.relay_with([1, 0], opener=FakeOpener(200))
		instance.reading()
		instance.reading()
		self.assertTrue(instance.deliver())
		request = instance.opener.sent[0]
		timestamp = request.get_header("X-h360-timestamp")
		expected = hmac.new(b"s3cret", timestamp.encode() + b"." + request.data, hashlib.sha256).hexdigest()
		self.assertEqual(request.get_header("X-h360-signature"), expected)
		self.assertLessEqual(abs(int(timestamp) - time.time()), 5)

	def test_kept_while_the_portal_is_down_with_the_same_message_id(self):
		opener = FakeOpener("unreachable", 503, 409)
		instance = self.relay_with([1, 0], opener=opener)
		instance.reading()
		instance.reading()
		self.assertFalse(instance.deliver())
		self.assertFalse(instance.deliver())
		self.assertEqual(len(self.spooled()), 1)
		self.assertTrue(instance.deliver(), "409: the server had it already")
		self.assertEqual(self.spooled(), [])
		ids = {json.loads(request.data)["message_id"] for request in opener.sent[1:]}
		self.assertEqual(len(ids), 1)

	def test_oldest_first_and_stop_at_the_first_that_must_wait(self):
		for value in (1, 0, 1):
			relay.spool_message(relay.new_message([{"metric": "uptime_external", "value": value}], self.settings), self.settings)
			time.sleep(0.01)
		opener = FakeOpener(200, 503)
		self.assertFalse(relay.flush(self.settings, opener=opener))
		self.assertEqual(len(self.spooled()), 2)
		self.assertEqual(json.loads(opener.sent[0].data)["samples"][0]["value"], 1)

	def test_permanent_rejections_are_dropped(self):
		relay.spool_message(relay.new_message([{"metric": "uptime_external", "value": 1}], self.settings), self.settings)
		self.assertTrue(relay.flush(self.settings, opener=FakeOpener(422)))
		self.assertEqual(self.spooled(), [])

	def test_spool_limits(self):
		self.settings.max_files = 2
		paths = []
		for _ in range(4):
			paths.append(relay.spool_message(relay.new_message([], self.settings), self.settings))
			time.sleep(0.01)
		old = time.time() - 40 * 86400
		os.utime(paths[-1], (old, old))
		relay.prune_spool(self.settings)
		self.assertEqual(self.spooled(), paths[1:3], "older than 35 days removed, then the oldest beyond the limit")

	def test_kuma_unreachable_records_nothing(self):
		def unreachable(settings):
			raise urllib.error.URLError("refused")

		instance = relay.Relay(self.settings, opener=FakeOpener())
		relay.read_kuma = unreachable
		self.assertEqual(instance.reading(), [])
		self.assertEqual(instance.buffer, [])


if __name__ == "__main__":
	result = unittest.main(argv=[sys.argv[0], "-v"], exit=False).result
	sys.exit(0 if result.wasSuccessful() else 1)
