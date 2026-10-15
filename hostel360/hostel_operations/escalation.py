"""Minute-by-minute escalation of open alerts along the escalation matrix.

Runs every minute from the scheduler. For each open or acknowledged alert it works out
which escalation levels have become due (after_minutes elapsed, or immediately for an
outage), whether a held-back re-open notice should now go out, and whether an
unacknowledged critical alert is due its reminder. Each alert is locked, updated and
committed on its own, and a per-minute run lock stops overlapping runs, so the job is safe
to run twice in the same minute: nobody is paged twice for the same step.
"""

import frappe
from frappe.utils import get_datetime, now_datetime

from hostel360.hostel_operations import notification
from hostel360.hostel_operations.constants import ACTIVE_STATUSES, ALERT_DOCTYPE, SETTINGS_DOCTYPE
from hostel360.hostel_operations.settings import get_settings

RUN_LOCK_SECONDS = 120
BATCH_LIMIT = 500


def run_escalations(now=None) -> dict | None:
	settings = get_settings()
	if not notification.notifications_enabled(settings):
		return None

	now = get_datetime(now) if now else now_datetime()
	if not _acquire_minute_lock(now):
		return None

	summary = escalate_open_alerts(settings, now)
	# Bookkeeping only: leaving `modified` alone keeps an operator's open settings form
	# saveable (a changed timestamp is what Frappe uses to detect a stale form).
	frappe.db.set_single_value(SETTINGS_DOCTYPE, "last_escalation_run_on", now, update_modified=False)
	_commit()
	return summary


def escalate_open_alerts(settings, now) -> dict:
	"""Process every open alert once. Returns counts by notification event."""
	names = frappe.get_all(
		ALERT_DOCTYPE,
		filters={"status": ("in", list(ACTIVE_STATUSES))},
		pluck="name",
		order_by="first_seen asc",
		limit=BATCH_LIMIT,
	)
	# Start each alert in a fresh transaction: alerts are written by other connections all
	# the time, and a snapshot taken before their commit would make the row lock below fail
	# with "record has changed since last read".
	_commit()
	summary: dict[str, int] = {}
	for name in names:
		try:
			event = _process(name, settings, now)
		except Exception:
			_rollback()
			frappe.log_error(
				title="Hostel360: alert escalation failed",
				message=frappe.get_traceback(),
				reference_doctype=ALERT_DOCTYPE,
				reference_name=name,
			)
			continue
		if event:
			summary[event] = summary.get(event, 0) + 1
		_commit()
	return summary


def _process(name: str, settings, now) -> str | None:
	# The row lock serialises concurrent runners; the state is re-read under the lock so
	# the second runner sees what the first one already sent.
	state = notification.get_alert_state(name, for_update=True)
	if not state or state.status not in ACTIVE_STATUSES:
		return None
	plan = notification.plan_notification(state, settings, now)
	if not plan:
		return None
	notification.record_plan(name, state, plan, now)
	notification.deliver(name, plan.event, plan.levels)
	return plan.event


def _acquire_minute_lock(now) -> bool:
	key = frappe.cache.make_key(f"hostel360:ops:escalation:{now:%Y%m%d%H%M}")
	try:
		return bool(frappe.cache.set(key, 1, nx=True, ex=RUN_LOCK_SECONDS))
	except Exception:
		# Without Redis there is no lock; the row locks still prevent duplicate pages.
		return True


def _commit() -> None:
	if not frappe.flags.in_test:
		frappe.db.commit()  # nosemgrep


def _rollback() -> None:
	if not frappe.flags.in_test:
		frappe.db.rollback()
