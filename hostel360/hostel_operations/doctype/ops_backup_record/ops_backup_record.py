# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, now_datetime


class OpsBackupRecord(Document):
	def validate(self):
		self.finished_at = self.finished_at or now_datetime()
		if self.started_at and get_datetime(self.started_at) > get_datetime(self.finished_at):
			frappe.throw(_("A backup cannot finish before it started."))
		if self.started_at:
			self.duration_seconds = int(
				(get_datetime(self.finished_at) - get_datetime(self.started_at)).total_seconds()
			)
		if flt(self.size_bytes) < 0:
			frappe.throw(_("Backup size cannot be negative."))
		self.size_display = human_size(self.size_bytes) if self.size_bytes else ""

	def after_insert(self):
		from hostel360.hostel_operations.alerts import raise_alert, resolve_alert

		key = failure_key(self.backup_type)
		if self.status == "Failed":
			label = (
				_("Restore test failed")
				if self.backup_type == "Restore Test"
				else _("{0} backup failed").format(_(self.backup_type))
			)
			raise_alert(
				key,
				label + (f" on {self.host}" if self.host else ""),
				"Critical",
				"Backup",
				source="Host Agent",
				description=self.message
				or _("The backup job reported a failure. See {0}.").format(self.name),
				details={
					"record": self.name,
					"repository": self.repository,
					"snapshot_id": self.snapshot_id,
					"started_at": str(self.started_at or ""),
					"finished_at": str(self.finished_at or ""),
				},
			)
			return

		note = _("Backup {0} succeeded at {1}.").format(self.name, self.finished_at)
		resolve_alert(key, notes=note, source="Host Agent")
		if self.backup_type == "Restore Test":
			resolve_alert("backup.restore_test.overdue", notes=note, source="Host Agent")
		else:
			resolve_alert("backup.stale", notes=note, source="Host Agent")


def failure_key(backup_type: str | None) -> str:
	return f"backup.{(backup_type or 'unknown').lower().replace(' ', '_')}.failed"


def human_size(size) -> str:
	size = flt(size)
	for unit in ("B", "KB", "MB", "GB", "TB"):
		if size < 1024 or unit == "TB":
			return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
		size /= 1024
	return ""
