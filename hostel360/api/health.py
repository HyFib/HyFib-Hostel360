"""Health endpoints for the load balancer, the uptime probe and the operations desk.

``liveness`` answers as long as the application process can serve a request; the OCI load
balancer uses it to decide whether to route traffic. ``readiness`` also checks the database
and Redis and returns 503 when either is down, which is what the external uptime probe
watches. Neither guest endpoint reveals anything beyond ok/degraded. ``status`` returns the
full component picture to HyFib Operations and the institution's auditors.
"""

import frappe
from frappe.rate_limiter import rate_limit

from hostel360.setup.roles import AUDITOR_ROLE, OPERATIONS_ROLE

STATUS_ROLES = (OPERATIONS_ROLE, AUDITOR_ROLE, "System Manager")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
@rate_limit(limit=600, seconds=60)
def liveness() -> dict:
	return {"status": "ok"}


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
@rate_limit(limit=300, seconds=60)
def readiness() -> dict:
	ready = _database_ready() and _redis_ready()
	if not ready:
		frappe.local.response.http_status_code = 503
	return {"status": "ok" if ready else "degraded"}


def _database_ready() -> bool:
	try:
		frappe.db.sql("select 1")
		return True
	except Exception:
		return False


def _redis_ready() -> bool:
	from frappe.utils.background_jobs import get_redis_conn

	try:
		frappe.cache.ping()
		get_redis_conn().ping()
		return True
	except Exception:
		return False


@frappe.whitelist(methods=["GET", "POST"])
@rate_limit(limit=60, seconds=60)
def status() -> dict:
	frappe.only_for(STATUS_ROLES)

	from hostel360.hostel_operations import health
	from hostel360.hostel_operations.alerts import open_alert_counts
	from hostel360.hostel_operations.settings import get_settings

	settings = get_settings()
	results = health.run_checks(settings, read_only=True)
	last_backup = frappe.get_all(
		"Ops Backup Record",
		filters={"status": "Success"},
		fields=["name", "backup_type", "finished_at", "size_display"],
		order_by="finished_at desc",
		limit=1,
	)
	return {
		"status": health.overall_status(results),
		"instance_label": settings.get("instance_label"),
		"environment": settings.get("environment"),
		"checked_at": str(frappe.utils.now_datetime()),
		"last_health_check_on": str(settings.get("last_health_check_on") or ""),
		"last_heartbeat_sent_on": str(settings.get("last_heartbeat_sent_on") or ""),
		"components": [result.as_dict() for result in results],
		"open_alerts": open_alert_counts(),
		"last_backup": last_backup[0] if last_backup else None,
		"versions": _versions(),
	}


def _versions() -> dict:
	import importlib

	versions = {}
	for app in frappe.get_installed_apps():
		try:
			versions[app] = getattr(importlib.import_module(app), "__version__", "")
		except ImportError:
			versions[app] = ""
	return versions
