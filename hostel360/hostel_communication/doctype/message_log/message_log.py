# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from hostel360.setup.roles import ADMIN_ROLE, OPERATIONS_ROLE

RETRY_ROLES = (ADMIN_ROLE, OPERATIONS_ROLE, "System Manager")


class MessageLog(Document):
	def onload(self):
		self.set_onload("can_retry", self.status == "Failed" and bool(self.payload))

	@frappe.whitelist(methods=["POST"])
	def retry_now(self) -> dict:
		"""Send a failed message again straight away (ignores the back-off schedule)."""
		from hostel360.hostel_communication.dispatch import retry_message

		frappe.only_for(RETRY_ROLES)
		result = retry_message(self.name, force=True)
		if result is None:
			frappe.throw(_("This message can no longer be retried."))
		return {"ok": result.ok, "error": result.error}

	@staticmethod
	def clear_old_logs(days: int = 365) -> None:
		from frappe.query_builder import Interval
		from frappe.query_builder.functions import Now

		table = frappe.qb.DocType("Message Log")
		frappe.db.delete(table, filters=(table.creation < (Now() - Interval(days=days))))
