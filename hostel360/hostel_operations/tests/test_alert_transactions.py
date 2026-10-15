"""Alert bookkeeping on a connection of its own, as in production (with real commits).

Every test here commits rows through other connections and removes them again afterwards
(see ``IndependentAlerts``); assertions read through a fresh connection for the same reason.
"""

import threading
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_operations import alerts, transaction
from hostel360.hostel_operations.tests.helpers import IndependentAlerts, committed_rows, unique_key

# Notifications are covered elsewhere; here they would only reach the site's real contacts.
QUIET_SETTINGS = frappe._dict(enabled=0)


class TestIndependentAlertTransactions(IntegrationTestCase):
	def setUp(self):
		patcher = patch.object(alerts, "get_settings", return_value=QUIET_SETTINGS)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_alert_survives_the_callers_rollback(self):
		key = unique_key("independent")
		with IndependentAlerts(key) as independent:
			frappe.db.savepoint("caller_work")
			name = alerts.raise_alert(key, "Webhook signature rejected", "Warning", "Payments")
			# The caller's request fails afterwards and Frappe rolls it back.
			frappe.db.rollback(save_point="caller_work")
			rows = independent.committed(key)
		self.assertTrue(name)
		self.assertEqual([row.name for row in rows], [name])

	def test_resolve_commits_on_its_own(self):
		key = unique_key("independent")
		with IndependentAlerts(key) as independent:
			name = alerts.raise_alert(key, "Disk filling up", "Warning", "Capacity")
			self.assertTrue(alerts.resolve_alert(key, notes="cleaned up"))
			self.assertFalse(alerts.resolve_alert(key), "nothing left open")
			rows = independent.committed(key)
		self.assertEqual([(row.name, row.status) for row in rows], [(name, "Resolved")])

	def test_deadlock_is_retried_without_touching_the_caller(self):
		key = unique_key("independent")
		real_record = alerts._record
		attempts = []

		def deadlock_once(*args, **kwargs):
			attempts.append(1)
			if len(attempts) == 1:
				raise frappe.QueryDeadlockError("1213 Deadlock found when trying to get lock")
			return real_record(*args, **kwargs)

		with IndependentAlerts(key) as independent, patch.object(alerts, "_record", side_effect=deadlock_once):
			todo = frappe.get_doc({"doctype": "ToDo", "description": key}).insert(ignore_permissions=True)
			name = alerts.raise_alert(key, "Gateway unreachable", "Critical", "Payments")
			rows = independent.committed(key)
		self.assertTrue(name)
		self.assertEqual(len(attempts), 2)
		self.assertEqual([row.name for row in rows], [name])
		self.assertTrue(frappe.db.exists("ToDo", todo.name), "the caller's own work is untouched")

	def test_failure_on_the_independent_connection_returns_empty(self):
		key = unique_key("independent")
		with (
			IndependentAlerts(key) as independent,
			patch.object(alerts, "_record", side_effect=frappe.QueryTimeoutError("1205 Lock wait timeout")),
		):
			self.assertEqual(alerts.raise_alert(key, "Broken", "Critical", "Other"), "")
			self.assertEqual(independent.committed(key), [])

	def test_no_second_connection_falls_back_to_the_callers_transaction(self):
		# Other tests here committed alerts from other connections after this transaction's
		# snapshot was taken; writing the same naming series row inline would then fail with
		# "record has changed since last read". Start from a fresh snapshot, as a new request does.
		frappe.db.rollback()
		# The inline write keeps the series lock until this transaction ends.
		self.addCleanup(frappe.db.rollback)
		key = unique_key("independent")
		with (
			IndependentAlerts(key) as independent,
			patch.object(transaction, "open_connection", side_effect=transaction.IndependentConnectionUnavailable("max")),
		):
			name = alerts.raise_alert(key, "Too many connections", "Warning", "Other")
			self.assertTrue(frappe.db.exists("Ops Alert", name))
		self.assertEqual(independent.committed(key), [], "written in the test's own transaction instead")
		# From here on this transaction holds the alert locks itself: stay inline, never wait on them.
		self.assertFalse(transaction.use_independent_transactions())

	def test_repeated_invalid_ingest_signatures_raise_an_alert_that_survives_the_401(self):
		"""Each bad request is rolled back by Frappe (401); the alert must not go with it."""
		import json

		from hostel360.api import ops
		from hostel360.hostel_operations.tests.test_ops_api import OpsRequestMixin, signed_headers

		requests = OpsRequestMixin()
		body = json.dumps({"type": "heartbeat", "host": "h360-prod"}).encode()
		bad = signed_headers(body, secret="not-the-secret")
		bucket = int(frappe.generate_hash(length=8), 16)
		key = ops.INVALID_SIGNATURE_ALERT_KEY
		agent_settings = frappe._dict(get_password=lambda *args, **kwargs: "the-real-agent-secret")
		try:
			with (
				patch.object(ops, "_invalid_signature_bucket", return_value=bucket),
				patch("hostel360.hostel_operations.settings.get_settings", return_value=agent_settings),
				IndependentAlerts(key) as independent,
			):
				before = {row.name: row.occurrence_count for row in independent.committed(key)}
				for _ in range(ops.INVALID_SIGNATURE_ALERT_THRESHOLD + 5):
					frappe.db.savepoint("bad_request")
					self.assertRaises(frappe.AuthenticationError, requests.ingest, {}, bad, body)
					frappe.db.rollback(save_point="bad_request")
				after = independent.committed(key)
		finally:
			frappe.local.request = None
			frappe.local.form_dict = frappe._dict()
		changed = [row for row in after if before.get(row.name) != row.occurrence_count]
		self.assertEqual(len(changed), 1, "recorded once for the window, and kept")
		self.assertEqual(changed[0].status, "Open")

	def test_concurrent_first_occurrences_keep_both_callers_work(self):
		"""Two workers raise the same new key at the same moment, each after doing its own work.

		Written inside the callers' transactions this deadlocked and InnoDB rolled back the
		loser's whole transaction (its earlier work silently lost, the alert not counted).
		"""
		key = unique_key("concurrent")
		marker = unique_key("caller-work")
		site, sites_path = frappe.local.site, frappe.local.sites_path
		barrier = threading.Barrier(2)
		real_recently_resolved = alerts._recently_resolved
		outcome = {}

		def after_lookup(lookup_key, now):
			# Hold each caller between looking the key up and inserting it until the other
			# has looked it up too: the interleaving that produced the deadlock.
			try:
				barrier.wait(timeout=3)
			except threading.BrokenBarrierError:
				pass
			return real_recently_resolved(lookup_key, now)

		def caller(index: int) -> None:
			frappe.init(site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.hostel360_independent_alerts = True
			try:
				frappe.get_doc({"doctype": "ToDo", "description": f"{marker} before {index}"}).insert(
					ignore_permissions=True
				)
				outcome[index] = alerts.raise_alert(key, "Provider failing", "Warning", "Messaging")
				frappe.get_doc({"doctype": "ToDo", "description": f"{marker} after {index}"}).insert(
					ignore_permissions=True
				)
				frappe.db.commit()
			except Exception as exc:
				outcome[index] = exc
			finally:
				frappe.destroy()

		try:
			with IndependentAlerts(key) as independent, patch.object(alerts, "_recently_resolved", side_effect=after_lookup):
				threads = [threading.Thread(target=caller, args=(index,)) for index in (1, 2)]
				for thread in threads:
					thread.start()
				for thread in threads:
					thread.join(timeout=60)
				rows = independent.committed(key)
				work = committed_rows(
					"select description from `tabToDo` where description like %(marker)s", {"marker": f"{marker}%"}
				)
		finally:
			_delete_todos(marker)

		self.assertEqual(outcome[1], outcome[2], outcome)
		self.assertTrue(outcome[1], outcome)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].occurrence_count, 2, "both occurrences are counted")
		self.assertEqual(len(work), 4, "neither caller lost its own work")


def _delete_todos(marker: str) -> None:
	db = transaction.open_connection()
	try:
		db.sql("delete from `tabToDo` where description like %(marker)s", {"marker": f"{marker}%"})
		db.commit()
	finally:
		db.close()
