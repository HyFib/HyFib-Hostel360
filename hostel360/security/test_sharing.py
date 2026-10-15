# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.security import files, sharing
from hostel360.setup.roles import ADMIN_ROLE, STUDENT_ROLE

SHARE_HOOK = "hostel360.security.sharing.validate_doc_share"
# Shares made by code paths that skip the sharer's own permission check.
UNCHECKED = {"ignore_share_permission": True}


def doc_hooks_with_share_rule() -> dict:
	"""The site's document hooks plus the DocShare rule (as hooks.py is asked to register it)."""
	hooks = dict(frappe.get_doc_hooks())
	docshare = dict(hooks.get("DocShare", {}))
	docshare["validate"] = [*docshare.get("validate", []), SHARE_HOOK]
	hooks["DocShare"] = docshare
	return hooks


def make_user(*roles: str, desk: bool = True) -> str:
	email = f"share-{frappe.generate_hash(length=8)}@example.com"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Share",
			"send_welcome_email": 0,
			"user_type": "System User" if desk else "Website User",
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	return user.name


def new_share(doctype: str, name: str, user: str | None = None, everyone: int = 0):
	return frappe.get_doc(
		{
			"doctype": "DocShare",
			"share_doctype": doctype,
			"share_name": name,
			"user": user,
			"everyone": everyone,
			"read": 1,
		}
	)


class TestSharingRules(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.hostel_doctype = "Message Template"
		self.hostel_record = frappe.get_all("Message Template", pluck="name", limit=1)[0]

	def test_protected_doctypes(self):
		self.assertTrue(sharing.is_protected_doctype("Message Template"))
		self.assertTrue(sharing.is_protected_doctype("Hostel Student"))
		self.assertTrue(sharing.is_protected_doctype("File"))
		self.assertTrue(sharing.is_protected_doctype("User"))
		self.assertFalse(sharing.is_protected_doctype("ToDo"))
		self.assertFalse(sharing.is_protected_doctype(None))

	def test_sharing_with_everyone_is_refused(self):
		with self.assertRaises(sharing.ShareNotAllowed):
			sharing.validate_doc_share(new_share(self.hostel_doctype, self.hostel_record, everyone=1))

	def test_sharing_with_a_portal_account_is_refused(self):
		student = make_user(STUDENT_ROLE, desk=False)
		with self.assertRaises(sharing.ShareNotAllowed):
			sharing.validate_doc_share(new_share(self.hostel_doctype, self.hostel_record, user=student))

	def test_sharing_with_a_desk_colleague_is_allowed(self):
		colleague = make_user(ADMIN_ROLE)
		sharing.validate_doc_share(new_share(self.hostel_doctype, self.hostel_record, user=colleague))

	def test_other_doctypes_and_self_shares_are_left_alone(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Share check"})
		todo.insert(ignore_permissions=True)
		sharing.validate_doc_share(new_share("ToDo", todo.name, everyone=1))
		student = make_user(STUDENT_ROLE, desk=False)
		# Frappe shares every User record with its own user.
		sharing.validate_doc_share(new_share("User", student, user=student))
		with self.assertRaises(sharing.ShareNotAllowed):
			sharing.validate_doc_share(new_share("User", student, everyone=1))

	def test_rule_runs_through_frappe_share_when_hooked(self):
		student = make_user(STUDENT_ROLE, desk=False)
		with patch.object(frappe, "get_doc_hooks", return_value=doc_hooks_with_share_rule()):
			with self.assertRaises(sharing.ShareNotAllowed):
				frappe.share.add(self.hostel_doctype, self.hostel_record, everyone=1, flags=UNCHECKED)
			with self.assertRaises(sharing.ShareNotAllowed):
				frappe.share.add(self.hostel_doctype, self.hostel_record, student, flags=UNCHECKED)
			# Creating a portal account still works: its self-share is allowed.
			make_user(STUDENT_ROLE, desk=False)
		self.assertFalse(
			frappe.db.exists(
				"DocShare",
				{"share_doctype": self.hostel_doctype, "share_name": self.hostel_record, "everyone": 1},
			)
		)

	def test_existing_unsafe_shares_are_removed(self):
		student = make_user(STUDENT_ROLE, desk=False)
		colleague = make_user(ADMIN_ROLE)
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Keep"}).insert(ignore_permissions=True)
		rows = {
			"everyone": new_share(self.hostel_doctype, self.hostel_record, everyone=1),
			"portal": new_share(self.hostel_doctype, self.hostel_record, user=student),
			"colleague": new_share(self.hostel_doctype, self.hostel_record, user=colleague),
			"todo": new_share("ToDo", todo.name, everyone=1),
		}
		for row in rows.values():
			row.db_insert()

		self.assertGreaterEqual(sharing.remove_unsafe_shares(), 2)
		self.assertFalse(frappe.db.exists("DocShare", rows["everyone"].name))
		self.assertFalse(frappe.db.exists("DocShare", rows["portal"].name))
		self.assertTrue(frappe.db.exists("DocShare", rows["colleague"].name))
		self.assertTrue(frappe.db.exists("DocShare", rows["todo"].name))
		# The share Frappe made of the student's own User record stays.
		self.assertTrue(
			frappe.db.exists("DocShare", {"share_doctype": "User", "share_name": student, "user": student})
		)
		self.assertEqual(sharing.remove_unsafe_shares(), 0)


class TestSecurityAlertDeadlocks(IntegrationTestCase):
	def test_deadlocks_reach_the_scan_job(self):
		deadlock = frappe.QueryDeadlockError("Deadlock found when trying to get lock")
		with patch("hostel360.hostel_operations.alerts.raise_alert", side_effect=deadlock):
			with self.assertRaises(frappe.QueryDeadlockError):
				files._alert("test-key", "Test alert", "Warning", "Test", {})
		with patch("hostel360.hostel_operations.alerts.raise_alert", side_effect=RuntimeError("down")):
			files._alert("test-key", "Test alert", "Warning", "Test", {})

	def test_deadlocks_while_resolving_the_scanner_alert(self):
		deadlock = frappe.QueryDeadlockError("Deadlock found when trying to get lock")
		key = frappe.cache.make_key(files.SCANNER_DOWN_KEY)
		frappe.cache.set(key, "1", ex=60)
		self.addCleanup(frappe.cache.delete, key)
		with patch("hostel360.hostel_operations.alerts.resolve_alert", side_effect=deadlock):
			with self.assertRaises(frappe.QueryDeadlockError):
				files._scanner_reachable()
