#!/usr/bin/env python3
"""Feed the Mumbai Uptime Kuma results into Hostel360 as ``uptime_external`` samples.

The on-host uptime probe runs on the server it measures, so it cannot see an outage of
that VM or of the Hyderabad region: those minutes end up "unmonitored" in the monthly
performance report. Uptime Kuma, in ap-mumbai-1, sees them. Once a minute this relay reads
the status of one Kuma monitor (default "Portal readiness") from Kuma's Prometheus endpoint
and records it:

	{"type": "metrics", "source": "Uptime Probe", "host": "<relay label>", "message_id": "<uuid4>",
	 "samples": [{"metric": "uptime_external", "value": 1 | 0, "recorded_at": "<ISO 8601>"},
	             {"metric": "external_response_ms", "value": <ms>, "unit": "ms", ...}]}

The messages go to the Hostel360 ingest API through the public load balancer, signed like the
host agent's (X-H360-Timestamp, X-H360-Signature = hex HMAC-SHA256 of "<timestamp>.<body>"
with the ingest secret). While the portal is down they cannot be delivered, which is exactly
when they matter, so every message is written to a spool first and sent oldest first once
the portal answers again; each keeps its message_id on every retry, so a retry of a message
that did arrive is answered 409 and not recorded twice.

Kuma status mapping: UP (1) -> 1; DOWN (0) and PENDING (2, a failed check being retried) -> 0;
MAINTENANCE (3) -> no sample, so those minutes are judged by the on-host probe. When Kuma
itself cannot be read nothing is recorded either.

Settings (environment):
	H360_INGEST_URL            https://<portal>/api/method/hostel360.api.ops.ingest (required)
	H360_INGEST_SECRET_FILE    file holding the ingest secret (default /run/secrets/h360_ingest_secret)
	H360_RELAY_HOST            host label on the samples (default uptime-kuma-mumbai)
	KUMA_METRICS_URL           default http://uptime-kuma:3001/metrics
	KUMA_API_KEY_FILE          file holding a Kuma API key (default /run/secrets/kuma_api_key)
	KUMA_MONITOR               monitor name (default "Portal readiness")
	RELAY_INTERVAL_SECONDS     default 60
	RELAY_BATCH                samples per message (default 5; sent at once on a change)
	RELAY_SPOOL_DIR            default /var/spool/h360-relay
	RELAY_SPOOL_KEEP_DAYS      default 35 (long enough for the monthly report)
	RELAY_SPOOL_MAX_FILES      default 12000

	h360-kuma-relay.py              run until stopped
	h360-kuma-relay.py --once       one reading and one delivery attempt (setup checks)
	h360-kuma-relay.py --show FILE  print what a saved /metrics page would record
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

LOG = logging.getLogger("h360-kuma-relay")

UP, DOWN, PENDING, MAINTENANCE = 1, 0, 2, 3
SERIES_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)(?:\s+\d+)?$")
LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
DELIVERED, RETRY, REJECTED = 0, 1, 2
USER_AGENT = "HyFib-Hostel360-Kuma-Relay/1.0"


class Settings:
	def __init__(self, env=None):
		env = os.environ if env is None else env
		self.ingest_url = env.get("H360_INGEST_URL", "").strip()
		self.secret_file = env.get("H360_INGEST_SECRET_FILE", "/run/secrets/h360_ingest_secret")
		self.host = env.get("H360_RELAY_HOST", "uptime-kuma-mumbai").strip()[:140] or "uptime-kuma-mumbai"
		self.metrics_url = env.get("KUMA_METRICS_URL", "http://uptime-kuma:3001/metrics")
		self.api_key_file = env.get("KUMA_API_KEY_FILE", "/run/secrets/kuma_api_key")
		self.monitor = env.get("KUMA_MONITOR", "Portal readiness")
		self.interval = _positive_int(env, "RELAY_INTERVAL_SECONDS", 60)
		self.batch = _positive_int(env, "RELAY_BATCH", 5)
		self.spool_dir = env.get("RELAY_SPOOL_DIR", "/var/spool/h360-relay")
		self.keep_days = _positive_int(env, "RELAY_SPOOL_KEEP_DAYS", 35)
		self.max_files = _positive_int(env, "RELAY_SPOOL_MAX_FILES", 12000)

	def require_ingest(self):
		if not self.ingest_url.startswith("https://"):
			raise SystemExit("H360_INGEST_URL must be the portal's https:// ingest URL")
		if not read_secret(self.secret_file):
			raise SystemExit(f"the ingest secret file {self.secret_file} is missing or empty")


def _positive_int(env, key, default):
	raw = env.get(key)
	if raw in (None, ""):
		return default
	try:
		value = int(raw)
	except ValueError:
		raise SystemExit(f"{key} must be a whole number") from None
	if value < 1:
		raise SystemExit(f"{key} must be at least 1")
	return value


def read_secret(path):
	try:
		with open(path, encoding="utf-8") as handle:
			return handle.read().strip()
	except OSError:
		return ""


# --------------------------------------------------------------------------- Kuma


def parse_metrics(text, monitor):
	"""Status and response time of one monitor from a Prometheus text page.

	Returns ``(status, response_ms)``; status is None when the monitor is not on the page.
	Label order and extra labels (Kuma versions differ) do not matter, only monitor_name.
	"""
	status = None
	response_ms = None
	for line in text.splitlines():
		line = line.strip()
		if not line or line.startswith("#"):
			continue
		match = SERIES_RE.match(line)
		if not match or match.group("name") not in ("monitor_status", "monitor_response_time"):
			continue
		labels = {key: _unescape(value) for key, value in LABEL_RE.findall(match.group("labels") or "")}
		if labels.get("monitor_name") != monitor:
			continue
		try:
			value = float(match.group("value"))
		except ValueError:
			continue
		if match.group("name") == "monitor_status":
			status = int(value)
		elif value >= 0:
			response_ms = value
	return status, response_ms


def _unescape(value):
	return value.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def samples_for(status, response_ms, recorded_at):
	"""What one reading records; an empty list for maintenance or an unknown status."""
	if status == UP:
		samples = [{"metric": "uptime_external", "value": 1, "recorded_at": recorded_at}]
		if response_ms is not None:
			samples.append(
				{"metric": "external_response_ms", "value": round(response_ms), "unit": "ms", "recorded_at": recorded_at}
			)
		return samples
	if status in (DOWN, PENDING):
		return [{"metric": "uptime_external", "value": 0, "recorded_at": recorded_at}]
	return []


def read_kuma(settings):
	request = urllib.request.Request(settings.metrics_url, headers={"User-Agent": USER_AGENT})
	api_key = read_secret(settings.api_key_file)
	if api_key:
		# Kuma reads an API key as the password of HTTP basic authentication; the user is ignored.
		token = base64.b64encode(f":{api_key}".encode()).decode()
		request.add_header("Authorization", f"Basic {token}")
	with urllib.request.urlopen(request, timeout=10) as response:
		return response.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------- spool and delivery


def new_message(samples, settings):
	return {
		"type": "metrics",
		"source": "Uptime Probe",
		"host": settings.host,
		"message_id": str(uuid.uuid4()),
		"samples": samples,
	}


def spool_message(message, settings):
	"""Write the message to the spool (atomically); the file name keeps creation order."""
	os.makedirs(settings.spool_dir, mode=0o700, exist_ok=True)
	stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
	path = os.path.join(settings.spool_dir, f"{stamp}-{message['message_id'][:8]}.json")
	tmp = os.path.join(settings.spool_dir, f".new-{message['message_id']}")
	with open(tmp, "w", encoding="utf-8") as handle:
		json.dump(message, handle, separators=(",", ":"))
	os.replace(tmp, path)
	return path


def spooled(settings):
	try:
		names = sorted(name for name in os.listdir(settings.spool_dir) if name.endswith(".json"))
	except FileNotFoundError:
		return []
	return [os.path.join(settings.spool_dir, name) for name in names]


def prune_spool(settings, now=None):
	now = time.time() if now is None else now
	files = spooled(settings)
	for path in files:
		try:
			if now - os.path.getmtime(path) > settings.keep_days * 86400:
				os.remove(path)
		except OSError:
			continue
	files = spooled(settings)
	excess = len(files) - settings.max_files
	if excess > 0:
		LOG.error("spool holds %s messages; dropping the %s oldest", len(files), excess)
		for path in files[:excess]:
			_remove(path)


def sign(secret, timestamp, body):
	return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def post(body, settings, opener=None, now=None):
	"""Send one message: DELIVERED (2xx, 409), REJECTED (400, 413, 422) or RETRY."""
	secret = read_secret(settings.secret_file)
	timestamp = str(int(time.time() if now is None else now))
	request = urllib.request.Request(
		settings.ingest_url,
		data=body,
		method="POST",
		headers={
			"Content-Type": "application/json",
			"User-Agent": USER_AGENT,
			"X-H360-Timestamp": timestamp,
			"X-H360-Signature": sign(secret, timestamp, body),
		},
	)
	opener = opener or urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
	try:
		with opener.open(request, timeout=15) as response:
			status = response.status
	except urllib.error.HTTPError as error:
		status = error.code
	except (urllib.error.URLError, OSError, TimeoutError) as error:
		LOG.warning("ingest unreachable: %s", getattr(error, "reason", error))
		return RETRY
	if 200 <= status < 300 or status == 409:
		return DELIVERED
	if status in (400, 413, 422):
		LOG.error("ingest rejected a message (HTTP %s); see Error Log in Hostel360", status)
		return REJECTED
	if status in (401, 403):
		LOG.error("ingest refused the signature (HTTP %s); check the ingest secret and the clock", status)
	else:
		LOG.warning("ingest answered HTTP %s; keeping the message", status)
	return RETRY


def flush(settings, opener=None, deadline=None):
	"""Send spooled messages oldest first; stop at the first that has to wait. True when empty."""
	prune_spool(settings)
	for path in spooled(settings):
		if deadline is not None and time.monotonic() >= deadline:
			return False
		try:
			with open(path, "rb") as handle:
				body = handle.read()
			json.loads(body)
		except (OSError, ValueError):
			LOG.error("dropping unreadable spool file %s", os.path.basename(path))
			_remove(path)
			continue
		result = post(body, settings, opener=opener)
		if result == RETRY:
			return False
		_remove(path)
	return True


def _remove(path):
	try:
		os.remove(path)
	except FileNotFoundError:
		pass


# --------------------------------------------------------------------------- main loop


class Relay:
	def __init__(self, settings, opener=None):
		self.settings = settings
		self.opener = opener
		self.buffer = []
		self.last_status = None
		self.missing_logged_at = 0.0

	def reading(self, now=None):
		"""Take one reading; returns the samples it produced."""
		now = time.time() if now is None else now
		recorded_at = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
		try:
			text = read_kuma(self.settings)
		except (urllib.error.URLError, OSError, TimeoutError) as error:
			LOG.warning("cannot read Uptime Kuma metrics: %s", getattr(error, "reason", error))
			return []
		status, response_ms = parse_metrics(text, self.settings.monitor)
		if status is None:
			if now - self.missing_logged_at > 3600:
				LOG.error("monitor %r is not in Uptime Kuma's metrics; check KUMA_MONITOR", self.settings.monitor)
				self.missing_logged_at = now
			return []
		samples = samples_for(status, response_ms, recorded_at)
		up = None if not samples else samples[0]["value"]
		changed = up is not None and self.last_status is not None and up != self.last_status
		if up is not None:
			if changed:
				LOG.info("portal is %s from Mumbai", "up" if up else "DOWN")
			self.last_status = up
		self.buffer.extend(samples)
		if self.buffer and (changed or len([s for s in self.buffer if s["metric"] == "uptime_external"]) >= self.settings.batch):
			self.spool_buffer()
		return samples

	def spool_buffer(self):
		if self.buffer:
			spool_message(new_message(self.buffer, self.settings), self.settings)
			self.buffer = []

	def deliver(self):
		# Leave time for the next reading even with a long backlog.
		return flush(self.settings, opener=self.opener, deadline=time.monotonic() + self.settings.interval * 0.7)


def run(settings):
	relay = Relay(settings)
	stopping = []

	def stop(_signum, _frame):
		stopping.append(True)

	signal.signal(signal.SIGTERM, stop)
	signal.signal(signal.SIGINT, stop)
	LOG.info("relaying monitor %r to %s every %ss", settings.monitor, settings.ingest_url, settings.interval)
	while not stopping:
		relay.reading()
		relay.deliver()
		# Read a little after each full minute, once Kuma's own check has run.
		pause = settings.interval - (time.time() % settings.interval) + min(15, settings.interval / 4)
		for _ in range(int(pause * 10)):
			if stopping:
				break
			time.sleep(0.1)
	relay.spool_buffer()
	LOG.info("stopped; %s message(s) wait in the spool", len(spooled(settings)))


def main(argv):
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
	settings = Settings()
	if len(argv) == 2 and argv[0] == "--show":
		with open(argv[1], encoding="utf-8") as handle:
			status, response_ms = parse_metrics(handle.read(), settings.monitor)
		now = datetime.now(timezone.utc).isoformat(timespec="seconds")
		print(json.dumps({"status": status, "samples": samples_for(status, response_ms, now)}, indent=1))
		return 0
	settings.require_ingest()
	if argv == ["--once"]:
		relay = Relay(settings)
		samples = relay.reading()
		relay.spool_buffer()
		delivered = relay.deliver()
		print(json.dumps({"samples": samples, "spool_empty": delivered}))
		return 0 if samples and delivered else 1
	if argv:
		print(__doc__)
		return 2
	run(settings)
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
