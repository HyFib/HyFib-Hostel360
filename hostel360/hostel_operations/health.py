"""Five-minute health checks of the Hostel360 installation.

Each check measures one thing, records it as Ops Metric Samples and opens or resolves its
own alert key, so a recovered component clears its alert without anyone touching it. The
same checks run read-only behind ``hostel360.api.health.status`` for the operations desk.

When every core check (database, Redis, job queue, scheduler, disk) is healthy the
configured dead-man switch URL is called. If the server, the scheduler or the alerting
path itself dies, those calls stop and the external monitor raises the alarm.
"""

import shutil
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from hostel360.hostel_operations.constants import (
	BACKUP_DOCTYPE,
	CRITICAL,
	METRIC_DOCTYPE,
	SETTINGS_DOCTYPE,
	WARNING,
)
from hostel360.hostel_operations.settings import get_settings, threshold

OK = "ok"
WARN = "warning"
CRIT = "critical"
UNKNOWN = "unknown"
STATUS_SEVERITY = {WARN: WARNING, CRIT: CRITICAL}
STATUS_RANK = {OK: 0, UNKNOWN: 0, WARN: 1, CRIT: 2}

ESCALATION_METHOD = "hostel360.hostel_operations.escalation.run_escalations"
FAILED_JOBS_CACHE_KEY = "hostel360:ops:failed_jobs_total"
TLS_CACHE_PREFIX = "hostel360:ops:tls:"
TLS_CACHE_SECONDS = 3600
RUN_LOCK_KEY = "hostel360:ops:health:lock"
RUN_LOCK_SECONDS = 240
HTTP_TIMEOUT_SECONDS = 5
BACKUP_TYPES = ("Daily", "Weekly", "Monthly", "Snapshot")


@dataclass
class CheckResult:
	key: str
	label: str
	category: str
	status: str = OK
	message: str = ""
	title: str = ""
	metrics: list = field(default_factory=list)
	details: dict | None = None
	core: bool = False
	is_outage: bool = False

	def as_dict(self) -> dict:
		return {
			"key": self.key,
			"label": self.label,
			"category": self.category,
			"status": self.status,
			"message": self.message,
			"metrics": [{"metric": m, "value": round(v, 3), "unit": u} for m, v, u in self.metrics],
			"core": self.core,
		}


def _level(value: float, warning: float, critical: float | None = None) -> str:
	if critical is not None and value >= critical:
		return CRIT
	if value >= warning:
		return WARN
	return OK


def _table_exists(doctype: str) -> bool:
	try:
		return frappe.db.table_exists(doctype)
	except Exception:
		return False


# Checks -------------------------------------------------------------------


def check_database(ctx) -> CheckResult:
	started = time.perf_counter()
	frappe.db.sql("select 1")
	elapsed = (time.perf_counter() - started) * 1000
	limit = threshold(ctx.settings, "db_latency_warning_ms")
	status = _level(elapsed, limit, limit * 4)
	return CheckResult(
		"health.database.latency",
		"Database",
		"Performance",
		status,
		f"Query round trip {elapsed:.0f} ms (warning above {limit:.0f} ms).",
		title="Database responding slowly",
		metrics=[("db_latency_ms", elapsed, "ms")],
		core=True,
	)


def _ping(label, key, connect, metric) -> CheckResult:
	started = time.perf_counter()
	try:
		connect().ping()
	except Exception as exc:
		return CheckResult(
			key,
			label,
			"Availability",
			CRIT,
			f"{label} is not responding ({exc.__class__.__name__}).",
			title=f"{label} is not responding",
			core=True,
		)
	elapsed = (time.perf_counter() - started) * 1000
	return CheckResult(
		key, label, "Availability", OK, f"Responded in {elapsed:.0f} ms.", metrics=[(metric, elapsed, "ms")], core=True
	)


def check_redis_cache(ctx) -> CheckResult:
	return _ping("Redis cache", "health.redis.cache", lambda: frappe.cache, "redis_cache_latency_ms")


def check_redis_queue(ctx) -> CheckResult:
	from frappe.utils.background_jobs import get_redis_conn

	return _ping("Redis job queue", "health.redis.queue", get_redis_conn, "redis_queue_latency_ms")


def check_job_queues(ctx) -> list[CheckResult]:
	from frappe.utils.background_jobs import get_queue, get_queue_list, get_redis_conn
	from rq import Worker

	backlog = 0
	failed = 0
	per_queue = {}
	for name in get_queue_list():
		queue = get_queue(name)
		count = queue.count
		backlog += count
		failed += queue.failed_job_registry.count
		per_queue[name] = count
	workers = len(Worker.all(connection=get_redis_conn()))

	limit = threshold(ctx.settings, "queue_backlog_threshold")
	backlog_status = _level(backlog, limit, limit * 4)
	if workers == 0:
		backlog_status = CRIT
	backlog_result = CheckResult(
		"health.jobs.backlog",
		"Background jobs",
		"Jobs",
		backlog_status,
		f"{backlog} jobs waiting, {workers} workers running."
		if workers
		else f"No background workers are running; {backlog} jobs waiting.",
		title="Background jobs are not being processed" if workers == 0 else "Background job backlog is growing",
		metrics=[("queue_backlog", backlog, "jobs"), ("rq_workers", workers, "workers")],
		details={"queues": per_queue, "workers": workers},
		core=True,
	)

	previous = None
	try:
		previous = frappe.cache.get_value(FAILED_JOBS_CACHE_KEY)
		if not ctx.read_only:
			frappe.cache.set_value(FAILED_JOBS_CACHE_KEY, failed, expires_in_sec=86400)
	except Exception:
		previous = None
	new_failures = max(failed - cint(previous), 0) if previous is not None else 0
	failed_limit = threshold(ctx.settings, "failed_job_threshold")
	failed_result = CheckResult(
		"health.jobs.failed",
		"Failed background jobs",
		"Jobs",
		_level(new_failures, failed_limit, failed_limit * 5),
		f"{new_failures} jobs failed since the previous check ({failed} in the failed registry).",
		title="Background jobs are failing",
		metrics=[("failed_jobs_new", new_failures, "jobs"), ("failed_jobs_total", failed, "jobs")],
	)
	return [backlog_result, failed_result]


def check_scheduler(ctx) -> CheckResult:
	from frappe.utils.scheduler import is_scheduler_disabled

	if frappe.conf.maintenance_mode or frappe.conf.pause_scheduler:
		return CheckResult(
			"health.scheduler", "Scheduler", "Jobs", UNKNOWN, "Scheduler paused for maintenance.", core=True
		)
	if is_scheduler_disabled(verbose=False):
		return CheckResult(
			"health.scheduler",
			"Scheduler",
			"Jobs",
			CRIT,
			"The scheduler is disabled: reminders, reconciliation, escalations and backup checks are not running.",
			title="Scheduler is disabled",
			core=True,
		)

	last = frappe.db.get_value("Scheduled Job Type", {"method": ESCALATION_METHOD}, "last_execution")
	if not last:
		latest = frappe.get_all(
			"Scheduled Job Type",
			filters={"stopped": 0, "last_execution": ("is", "set")},
			fields=["last_execution"],
			order_by="last_execution desc",
			limit=1,
		)
		last = latest[0].last_execution if latest else None
	if not last:
		return CheckResult(
			"health.scheduler", "Scheduler", "Jobs", UNKNOWN, "No scheduled job has run yet.", core=True
		)
	lag = (ctx.now - get_datetime(last)).total_seconds() / 60
	limit = threshold(ctx.settings, "scheduler_stale_minutes")
	status = CRIT if lag > limit else OK
	return CheckResult(
		"health.scheduler",
		"Scheduler",
		"Jobs",
		status,
		f"Minute jobs last ran {lag:.0f} minutes ago.",
		title="Scheduler has stopped running jobs",
		metrics=[("scheduler_lag_minutes", max(lag, 0), "min")],
		core=True,
	)


def check_error_spike(ctx) -> CheckResult:
	since = add_to_date(ctx.now, minutes=-15)
	count = frappe.db.count("Error Log", {"creation": (">=", since)})
	limit = threshold(ctx.settings, "error_spike_threshold")
	details = None
	if count >= limit:
		top = frappe.get_all(
			"Error Log",
			filters={"creation": (">=", since)},
			fields=["method", {"COUNT": "*", "as": "count"}],
			group_by="method",
		)
		top = sorted(top, key=lambda row: row["count"], reverse=True)[:5]
		details = {"top_errors": [{"title": row.method, "count": row["count"]} for row in top]}
	return CheckResult(
		"health.errors.spike",
		"Application errors",
		"Errors",
		_level(count, limit, limit * 4),
		f"{count} errors logged in the last 15 minutes.",
		title="Spike in application errors",
		metrics=[("error_log_15m", count, "errors")],
		details=details,
	)


def check_failed_logins(ctx) -> CheckResult:
	since = add_to_date(ctx.now, minutes=-15)
	filters = {"operation": "Login", "status": "Failed", "creation": (">=", since)}
	count = frappe.db.count("Activity Log", filters)
	limit = threshold(ctx.settings, "failed_login_threshold")
	details = None
	if count >= limit:
		ips = frappe.get_all(
			"Activity Log",
			filters=filters,
			fields=["ip_address", {"COUNT": "*", "as": "count"}],
			group_by="ip_address",
		)
		ips = sorted(ips, key=lambda row: row["count"], reverse=True)[:5]
		users = frappe.get_all("Activity Log", filters=filters, fields=["user"], distinct=True, limit=1000)
		details = {
			"top_ip_addresses": [{"ip": row.ip_address, "count": row["count"]} for row in ips],
			"distinct_users": len(users),
		}
	return CheckResult(
		"security.failed_logins",
		"Failed logins",
		"Security",
		_level(count, limit, limit * 4),
		f"{count} failed logins in the last 15 minutes.",
		title="Unusual number of failed logins",
		metrics=[("failed_logins_15m", count, "logins")],
		details=details,
	)


def check_payments(ctx) -> list[CheckResult]:
	doctype = "Hostel Payment Transaction"
	if not _table_exists(doctype):
		return []
	minutes = threshold(ctx.settings, "pending_payment_minutes")
	stale = frappe.db.count(
		doctype, {"status": "Pending", "creation": ("<", add_to_date(ctx.now, minutes=-minutes))}
	)
	failures = frappe.db.count(doctype, {"status": "Failed", "modified": (">=", add_to_date(ctx.now, hours=-1))})
	failure_limit = threshold(ctx.settings, "payment_failure_threshold")
	return [
		CheckResult(
			"payments.pending_stale",
			"Pending payments",
			"Payments",
			WARN if stale else OK,
			f"{stale} online payments pending for more than {minutes:.0f} minutes.",
			title="Online payments stuck in pending",
			metrics=[("payments_pending_stale", stale, "payments")],
		),
		CheckResult(
			"payments.failures",
			"Payment failures",
			"Payments",
			_level(failures, failure_limit, failure_limit * 3),
			f"{failures} failed online payments in the last hour.",
			title="Unusual number of failed online payments",
			metrics=[("payment_failures_1h", failures, "payments")],
		),
	]


def check_messaging(ctx) -> CheckResult | None:
	doctype = "Message Log"
	if not _table_exists(doctype):
		return None
	since = add_to_date(ctx.now, hours=-1)
	sent = frappe.db.count(doctype, {"status": "Sent", "creation": (">=", since)})
	failed = frappe.db.count(doctype, {"status": "Failed", "creation": (">=", since)})
	attempts = sent + failed
	percent = failed * 100.0 / attempts if attempts else 0.0
	limit = threshold(ctx.settings, "message_failure_percent")
	status = OK
	# A couple of failures out of three messages at night is not a trend.
	if attempts >= 10:
		status = _level(percent, limit, max(50.0, limit * 2))
	return CheckResult(
		"messaging.failure_rate",
		"SMS / WhatsApp / e-mail delivery",
		"Messaging",
		status,
		f"{failed} of {attempts} messages failed in the last hour ({percent:.0f}%).",
		title="Messages are failing to deliver",
		metrics=[("message_failure_percent", percent, "%")],
	)


def check_backups(ctx) -> list[CheckResult]:
	if ctx.settings.get("environment") == "Development":
		return []
	results = []
	last = frappe.get_all(
		BACKUP_DOCTYPE,
		filters={"status": "Success", "backup_type": ("in", BACKUP_TYPES)},
		fields=["name", "finished_at", "backup_type"],
		order_by="finished_at desc",
		limit=1,
	)
	max_age = threshold(ctx.settings, "backup_max_age_hours")
	if not last:
		results.append(
			CheckResult(
				"backup.stale",
				"Backups",
				"Backup",
				CRIT,
				"No successful backup has been recorded yet.",
				title="No successful backup recorded",
			)
		)
	else:
		age = (ctx.now - get_datetime(last[0].finished_at)).total_seconds() / 3600
		results.append(
			CheckResult(
				"backup.stale",
				"Backups",
				"Backup",
				CRIT if age > max_age else OK,
				f"Last successful backup {age:.1f} hours ago ({last[0].name}).",
				title=f"No successful backup in the last {max_age:.0f} hours",
				metrics=[("backup_age_hours", age, "h")],
			)
		)

	restore = frappe.get_all(
		BACKUP_DOCTYPE,
		filters={"status": "Success", "backup_type": "Restore Test"},
		fields=["finished_at"],
		order_by="finished_at desc",
		limit=1,
	)
	max_days = threshold(ctx.settings, "restore_test_max_age_days")
	if restore:
		days = (ctx.now - get_datetime(restore[0].finished_at)).total_seconds() / 86400
		message = f"Last successful restore test {days:.0f} days ago."
		status = WARN if days > max_days else OK
	else:
		message = "No successful restore test has been recorded yet."
		status = WARN
	results.append(
		CheckResult(
			"backup.restore_test.overdue",
			"Restore tests",
			"Backup",
			status,
			message,
			title="Monthly restore test is overdue",
		)
	)
	return results


def check_disk(ctx) -> CheckResult:
	usage = shutil.disk_usage(frappe.get_site_path())
	percent = usage.used * 100.0 / usage.total if usage.total else 0.0
	warning = threshold(ctx.settings, "disk_warning_percent")
	critical = threshold(ctx.settings, "disk_critical_percent")
	free_gb = usage.free / (1024**3)
	return CheckResult(
		"capacity.disk.sites",
		"Disk (site files)",
		"Capacity",
		_level(percent, warning, critical),
		f"{percent:.1f}% used, {free_gb:.1f} GB free.",
		title="Disk space running low",
		metrics=[("sites_disk_percent", percent, "%")],
		core=True,
	)


def _portal_url() -> str | None:
	if not frappe.db.exists("DocType", "Hostel Settings"):
		return None
	try:
		return frappe.db.get_single_value("Hostel Settings", "portal_url")
	except Exception:
		return None


def check_tls(ctx) -> CheckResult | None:
	url = _portal_url()
	if not url:
		return None
	parsed = urlparse(url if "://" in url else f"https://{url}")
	if parsed.scheme != "https" or not parsed.hostname:
		return None
	host, port = parsed.hostname, parsed.port or 443

	cache_key = f"{TLS_CACHE_PREFIX}{host}:{port}"
	cached = None
	try:
		cached = frappe.cache.get_value(cache_key)
	except Exception:
		cached = None
	if cached is None:
		cached = _inspect_certificate(host, port)
		try:
			frappe.cache.set_value(cache_key, cached, expires_in_sec=TLS_CACHE_SECONDS)
		except Exception:
			pass

	warning_days = threshold(ctx.settings, "ssl_expiry_warning_days")
	result = CheckResult(
		"security.tls.expiry", "TLS certificate", "Security", UNKNOWN, title=f"TLS certificate problem for {host}"
	)
	if cached.get("error"):
		result.message = f"Could not reach {host}:{port} to inspect the certificate ({cached['error']})."
		return result
	if cached.get("invalid"):
		result.status = CRIT
		result.message = f"The certificate for {host} failed verification: {cached['invalid']}."
		return result
	days = (cached["not_after"] - time.time()) / 86400
	result.metrics = [("tls_days_remaining", days, "days")]
	result.details = {"host": host, "expires_on": cached.get("not_after_text")}
	if days <= 7:
		result.status = CRIT
	elif days <= warning_days:
		result.status = WARN
	else:
		result.status = OK
	result.message = f"Certificate for {host} expires in {days:.0f} days ({cached.get('not_after_text')})."
	result.title = f"TLS certificate for {host} expires in {max(days, 0):.0f} days"
	return result


def _inspect_certificate(host: str, port: int) -> dict:
	context = ssl.create_default_context()
	try:
		with socket.create_connection((host, port), timeout=HTTP_TIMEOUT_SECONDS) as sock:
			with context.wrap_socket(sock, server_hostname=host) as tls:
				cert = tls.getpeercert()
	except ssl.SSLCertVerificationError as exc:
		return {"invalid": exc.verify_message or "verification failed"}
	except (OSError, ssl.SSLError) as exc:
		return {"error": exc.__class__.__name__}
	not_after = cert.get("notAfter")
	return {"not_after": ssl.cert_time_to_seconds(not_after), "not_after_text": not_after}


def check_agents(ctx) -> list[CheckResult]:
	"""Host agents send a heartbeat every minute; a silent agent means a blind spot."""
	table = frappe.qb.DocType(METRIC_DOCTYPE)
	from pypika.functions import Max

	rows = (
		frappe.qb.from_(table)
		.select(table.host, Max(table.recorded_at).as_("last_seen"))
		.where((table.metric == "agent_heartbeat") & (table.recorded_at >= add_to_date(ctx.now, days=-1)))
		.groupby(table.host)
	).run(as_dict=True)
	limit = threshold(ctx.settings, "agent_silence_minutes")
	results = []
	for row in rows:
		host = row.host or "unknown"
		silent = (ctx.now - get_datetime(row.last_seen)).total_seconds() / 60
		results.append(
			CheckResult(
				f"agent.heartbeat.{host}",
				f"Host agent {host}",
				"Availability",
				WARN if silent > limit else OK,
				f"Last heartbeat {silent:.0f} minutes ago.",
				title=f"Host agent on {host} has stopped reporting",
			)
		)
	return results


CHECKS = (
	check_database,
	check_redis_cache,
	check_redis_queue,
	check_job_queues,
	check_scheduler,
	check_disk,
	check_error_spike,
	check_failed_logins,
	check_payments,
	check_messaging,
	check_backups,
	check_tls,
	check_agents,
)


# Runner -------------------------------------------------------------------


def run_checks(settings=None, now=None, read_only: bool = False) -> list[CheckResult]:
	ctx = frappe._dict(settings=settings or get_settings(), now=get_datetime(now) if now else now_datetime(), read_only=read_only)
	results = []
	for check in CHECKS:
		try:
			outcome = check(ctx)
		except Exception:
			if not read_only:
				frappe.log_error(title=f"Hostel360: health check {check.__name__} failed", message=frappe.get_traceback())
			outcome = CheckResult(
				f"health.check.{check.__name__}",
				check.__name__.removeprefix("check_").replace("_", " ").capitalize(),
				"Other",
				UNKNOWN,
				"The check could not run; see the Error Log.",
			)
		if outcome is None:
			continue
		results.extend(outcome if isinstance(outcome, list) else [outcome])
	return results


def run_health_checks() -> dict | None:
	"""Scheduler entry point (every five minutes)."""
	if not _acquire_lock():
		return None
	try:
		return _run_and_apply()
	finally:
		_release_lock()


def _run_and_apply(now=None) -> dict:
	from hostel360.hostel_operations.alerts import raise_alert, resolve_alert
	from hostel360.hostel_operations.metrics import record_metrics

	settings = get_settings()
	now = get_datetime(now) if now else now_datetime()
	results = run_checks(settings, now)

	samples = [
		{"metric": metric, "value": value, "unit": unit, "recorded_at": now}
		for result in results
		for metric, value, unit in result.metrics
	]
	samples.append({"metric": "app_ready", "value": 1 if _ready(results) else 0, "unit": "", "recorded_at": now})
	try:
		record_metrics(samples, source="Health Check", host=socket.gethostname())
		_commit()
	except Exception:
		frappe.log_error(title="Hostel360: could not record health metrics", message=frappe.get_traceback())

	for result in results:
		if result.status in STATUS_SEVERITY:
			raise_alert(
				result.key,
				result.title or result.label,
				STATUS_SEVERITY[result.status],
				result.category,
				source="Health Check",
				description=result.message,
				details=result.details,
				is_outage=result.is_outage,
			)
		elif result.status == OK:
			resolve_alert(result.key, notes=f"Recovered: {result.message}", source="Health Check")
		_commit()

	frappe.db.set_single_value(SETTINGS_DOCTYPE, "last_health_check_on", now)
	heartbeat = None
	if all(r.status != CRIT for r in results if r.core):
		heartbeat = send_heartbeat(settings, now)
	_commit()
	return {
		"checked_at": str(now),
		"results": [r.as_dict() for r in results],
		"heartbeat": heartbeat,
	}


def _ready(results: list[CheckResult]) -> bool:
	essential = {"health.database.latency", "health.redis.cache", "health.redis.queue"}
	return all(r.status != CRIT for r in results if r.key in essential)


def send_heartbeat(settings, now) -> bool | None:
	import requests

	url = (settings.get("heartbeat_url") or "").strip()
	if not url:
		return None
	if not (url.startswith("https://") or (settings.get("environment") == "Development" and url.startswith("http://"))):
		frappe.log_error(title="Hostel360: heartbeat URL must use https", message="Update Ops Alert Settings.")
		return False
	try:
		response = requests.get(
			url, timeout=HTTP_TIMEOUT_SECONDS, headers={"User-Agent": "HyFib-Hostel360-Health/1.0"}
		)
		ok = response.status_code < 400
	except requests.RequestException as exc:
		frappe.log_error(title="Hostel360: heartbeat ping failed", message=exc.__class__.__name__)
		return False
	if ok:
		frappe.db.set_single_value(SETTINGS_DOCTYPE, "last_heartbeat_sent_on", now)
	return ok


def overall_status(results: list[CheckResult]) -> str:
	worst = max((STATUS_RANK.get(r.status, 0) for r in results), default=0)
	return {0: OK, 1: WARN, 2: CRIT}[worst]


def _acquire_lock() -> bool:
	try:
		return bool(frappe.cache.set(frappe.cache.make_key(RUN_LOCK_KEY), 1, nx=True, ex=RUN_LOCK_SECONDS))
	except Exception:
		return True


def _release_lock() -> None:
	try:
		frappe.cache.delete(frappe.cache.make_key(RUN_LOCK_KEY))
	except Exception:
		pass


def _commit() -> None:
	if not frappe.flags.in_test:
		frappe.db.commit()  # nosemgrep
