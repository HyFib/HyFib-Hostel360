"""Short database transactions that are independent of the caller's transaction.

Alert bookkeeping is called from payment webhooks, message delivery jobs, guest API
requests and error handlers. Writing the alert inside the caller's transaction ties the two
together in ways nobody wants:

* two callers raising the same key at the same moment can deadlock (or, with MariaDB's
  snapshot isolation, fail with "record has changed since last read"), and InnoDB then rolls
  back the *caller's* whole transaction, silently discarding work the caller already did;
* a request that ends in an error (a rejected signature, a failed webhook) rolls the alert
  back together with everything else, so the very failures worth reporting vanish.

``independent()`` opens a second connection to the same database and makes it the current
``frappe.db`` for the duration of the block, so ordinary document code runs unchanged but
commits on its own. The caller's connection is restored afterwards, whatever happens.
"""

import hashlib
from contextlib import contextmanager, suppress

import frappe

# Alert writes are tiny; waiting longer than this for a row lock means something else is
# wrong, and the caller is better served by a quick failure it can retry.
LOCK_WAIT_SECONDS = 10
NAMED_LOCK_SECONDS = 10


class IndependentConnectionUnavailable(Exception):
	"""A second database connection could not be opened (for example, connection limit)."""


def use_independent_transactions() -> bool:
	"""False in tests, which run inside one transaction that is rolled back afterwards.

	Tests that exercise the independent path set ``frappe.flags.hostel360_independent_alerts``.
	Also False once the current transaction has itself written alert rows (see
	``mark_alert_rows_written_inline``).
	"""
	if frappe.flags.hostel360_alerts_inline:
		return False
	if frappe.flags.hostel360_independent_alerts:
		return True
	if frappe.flags.in_test or getattr(frappe, "in_test", False):
		return False
	return bool(getattr(frappe.local, "db", None))


def mark_alert_rows_written_inline() -> None:
	"""Keep the rest of this transaction's alert writes inline.

	A transaction that has inserted or updated alert rows itself holds their row and
	``open_key`` index locks until it ends. A second connection of the same process would
	wait for those locks until it timed out, so later alerts in this transaction are written
	in it too. The mark is cleared when the transaction commits or rolls back.
	"""
	if frappe.flags.hostel360_independent_depth or frappe.flags.hostel360_alerts_inline:
		return
	db = getattr(frappe.local, "db", None)
	if not db:
		return
	frappe.flags.hostel360_alerts_inline = True

	def clear():
		frappe.flags.hostel360_alerts_inline = False

	db.after_commit.add(clear)
	db.after_rollback.add(clear)


def open_connection():
	"""A new, connected database object with the same credentials as the primary connection."""
	from frappe.database import get_db

	# A read-only request may have swapped frappe.db for the replica; writes go to the primary.
	source = getattr(frappe.local, "primary_db", None) or frappe.local.db
	try:
		db = get_db(
			socket=source.socket,
			host=source.host,
			user=source.user,
			password=source.password,
			port=source.port,
			cur_db_name=source.cur_db_name,
		)
		db.connect()
		db.sql("set session innodb_lock_wait_timeout = %s", LOCK_WAIT_SECONDS)
	except Exception as exc:
		raise IndependentConnectionUnavailable(exc.__class__.__name__) from exc
	return db


@contextmanager
def independent():
	"""Run the block on its own connection; the block commits (or rolls back) explicitly.

	Anything left uncommitted when the block ends is rolled back.
	"""
	main = frappe.local.db
	db = open_connection()
	# Realtime events are buffered per request and flushed by whichever connection created
	# the buffer; keep the two transactions' events apart so neither flushes the other's.
	had_realtime = hasattr(frappe.local, "_realtime_log")
	outer_realtime = frappe.local._realtime_log if had_realtime else None
	if had_realtime:
		del frappe.local._realtime_log
	frappe.local.db = db
	frappe.flags.hostel360_independent_depth = (frappe.flags.hostel360_independent_depth or 0) + 1
	try:
		yield db
	finally:
		frappe.flags.hostel360_independent_depth -= 1
		frappe.local.db = main
		# Cleanup must not mask the block's own exception; a broken connection is discarded anyway.
		with suppress(Exception):
			db.rollback()
		with suppress(Exception):
			db.close()
		if hasattr(frappe.local, "_realtime_log"):
			del frappe.local._realtime_log
		if had_realtime:
			frappe.local._realtime_log = outer_realtime


def lock_name(key: str, db=None) -> str:
	"""Server-wide lock name for ``key``; MariaDB named locks span every database on the server."""
	db = db or frappe.db
	digest = hashlib.sha1(f"{db.cur_db_name}:{key}".encode(), usedforsecurity=False).hexdigest()
	return f"h360a:{digest}"


def acquire_named_lock(name: str, timeout: int = NAMED_LOCK_SECONDS) -> bool:
	result = frappe.db.sql("select get_lock(%s, %s)", (name, timeout))
	return bool(result and result[0][0] == 1)


def release_named_lock(name: str) -> None:
	# The lock goes with the connection anyway.
	with suppress(Exception):
		frappe.db.sql("select release_lock(%s)", (name,))
