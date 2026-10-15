"""Housekeeping for operations data."""

import frappe
from frappe.utils import add_days, now_datetime

from hostel360.hostel_operations.constants import METRIC_DOCTYPE

RECEIPT_DOCTYPE = "Ops Ingest Receipt"

METRIC_RETENTION_DAYS = 400
PURGE_BATCH_SIZE = 10_000
# Receipts only need to outlive the agent's retries: the 300-second signature window, and
# the 24 hours a message may wait in the agent's spool before it is re-sent.
RECEIPT_RETENTION_DAYS = 3


def purge_old_metric_samples(retention_days: int = METRIC_RETENTION_DAYS, batch_size: int = PURGE_BATCH_SIZE) -> int:
	"""Delete metric samples older than the retention period, in batches.

	400 days keeps a full year for the monthly reports plus the 180-day log retention the
	CERT-In directions require. Batches keep each transaction (and its undo log) small on a
	table that grows by tens of thousands of rows a day.
	"""
	cutoff = add_days(now_datetime(), -retention_days)
	table = frappe.qb.DocType(METRIC_DOCTYPE)
	deleted = 0
	while True:
		names = (
			frappe.qb.from_(table).select(table.name).where(table.recorded_at < cutoff).limit(batch_size)
		).run(pluck=True)
		if not names:
			break
		frappe.qb.from_(table).delete().where(table.name.isin(names)).run()
		deleted += len(names)
		if not frappe.flags.in_test:
			frappe.db.commit()  # nosemgrep
		if len(names) < batch_size:
			break
	if deleted:
		frappe.logger("hostel360").info(f"Purged {deleted} ops metric samples older than {retention_days} days")

	# The same daily run keeps the ingest receipt table small.
	try:
		purge_ingest_receipts(batch_size=batch_size)
	except Exception:
		frappe.log_error(title="Hostel360: could not purge ingest receipts", message=frappe.get_traceback())
	return deleted


def purge_ingest_receipts(retention_days: int = RECEIPT_RETENTION_DAYS, batch_size: int = PURGE_BATCH_SIZE) -> int:
	"""Delete host agent receipts that no retry can refer to any more."""
	if not frappe.db.table_exists(RECEIPT_DOCTYPE):
		return 0
	cutoff = add_days(now_datetime(), -retention_days)
	table = frappe.qb.DocType(RECEIPT_DOCTYPE)
	deleted = 0
	while True:
		names = (
			frappe.qb.from_(table).select(table.name).where(table.received_at < cutoff).limit(batch_size)
		).run(pluck=True)
		if not names:
			break
		frappe.qb.from_(table).delete().where(table.name.isin(names)).run()
		deleted += len(names)
		if not frappe.flags.in_test:
			frappe.db.commit()  # nosemgrep
		if len(names) < batch_size:
			break
	return deleted
