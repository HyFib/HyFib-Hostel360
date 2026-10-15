"""Desk permissions of the operations DocTypes."""

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.setup.roles import AUDITOR_ROLE

# Design section 3: the Institution Auditor reads, prints and exports; nothing else.
FORBIDDEN_FOR_AUDITOR = ("write", "create", "delete", "submit", "cancel", "amend", "share", "email", "import")


class TestOperationsPermissions(IntegrationTestCase):
	def test_institution_auditor_is_read_only(self):
		doctypes = frappe.get_all("DocType", filters={"module": "Hostel Operations", "istable": 0}, pluck="name")
		self.assertIn("Security Incident", doctypes)
		checked = 0
		for doctype in doctypes:
			for perm in frappe.get_meta(doctype).permissions:
				if perm.role != AUDITOR_ROLE:
					continue
				checked += 1
				granted = [right for right in FORBIDDEN_FOR_AUDITOR if perm.get(right)]
				# Share in particular would let an auditor grant read access to anyone.
				self.assertFalse(granted, f"{doctype}: Institution Auditor has {', '.join(granted)}")
		self.assertGreater(checked, 0)

	def test_security_incident_access(self):
		perms = {perm.role: perm for perm in frappe.get_meta("Security Incident").permissions if not perm.permlevel}
		self.assertTrue(perms["HyFib Operations"].write and perms["HyFib Operations"].create)
		self.assertFalse(perms["HyFib Operations"].delete, "incident records are kept for the audit trail")
		for role in (AUDITOR_ROLE, "Hostel Administrator"):
			self.assertTrue(perms[role].read)
			self.assertFalse(perms[role].write or perms[role].create)
