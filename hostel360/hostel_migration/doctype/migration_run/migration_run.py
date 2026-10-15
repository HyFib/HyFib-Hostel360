# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from hostel360.setup.roles import ADMIN_ROLES
from hostel360.utils.context import has_any_role

FINISHED_STATUSES = ("Completed", "Completed with Errors", "Failed")


class MigrationRun(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from hostel360.hostel_migration.doctype.migration_run_entity.migration_run_entity import (
			MigrationRunEntity,
		)

		attachments_verified: DF.Int
		dry_run: DF.Check
		entities: DF.Table[MigrationRunEntity]
		entity_filter: DF.SmallText | None
		finished_on: DF.Datetime | None
		log: DF.Code | None
		mapping: DF.Code
		mapping_checksum: DF.Data | None
		mapping_name: DF.Data | None
		naming_series: DF.Literal["MIG-.YYYY.-.#####"]
		reconciliation: DF.Code | None
		reconciliation_status: DF.Literal["Not Run", "Matched", "Mismatched"]
		report_file: DF.Attach | None
		report_json: DF.Attach | None
		report_markdown: DF.Attach | None
		source_path: DF.Data
		source_system: DF.Data | None
		started_by: DF.Link | None
		started_on: DF.Datetime | None
		status: DF.Literal["Draft", "Running", "Completed", "Completed with Errors", "Failed"]
		title: DF.Data | None
		total_created: DF.Int
		total_failed: DF.Int
		total_skipped: DF.Int
		total_source_rows: DF.Int
		total_updated: DF.Int
	# end: auto-generated types

	def validate(self):
		if self.flags.from_engine:
			return
		if self.is_new():
			self.status = "Draft"
		elif self.has_value_changed("mapping") or self.has_value_changed("source_path"):
			if self.get_db_value("status") != "Draft":
				frappe.throw(_("A run that has started cannot be changed. Use Run Again to start a new run."))
		self.validate_mapping()
		self.entity_filter = "\n".join(self.get_entity_filter()) or None

	def validate_mapping(self):
		from hostel360.hostel_migration.mapping import MappingError, load_mapping

		try:
			mapping = load_mapping(self.mapping or "")
		except MappingError as e:
			frappe.throw(str(e), title=_("Mapping is not valid"))
		self.mapping_name = mapping.name
		self.source_system = mapping.source_system
		self.mapping_checksum = mapping.checksum
		unknown = sorted(set(self.get_entity_filter()) - set(mapping.entities))
		if unknown:
			frappe.throw(
				_("Unknown entities in Only These Entities: {0}. The mapping defines: {1}.").format(
					", ".join(unknown), ", ".join(mapping.load_order)
				)
			)

	def get_entity_filter(self) -> list[str]:
		from hostel360.hostel_migration.engine import parse_entity_filter

		return parse_entity_filter(self.entity_filter)

	def on_trash(self):
		if self.status == "Running" and not self.release_if_stale():
			frappe.throw(_("A running migration cannot be deleted."))

	def release_if_stale(self) -> bool:
		"""A run left "Running" by a process that died (server restart) is marked Failed.

		The engine holds a database lock while it works, so a free lock means nothing is
		really running. Returns True when the run was stale.
		"""
		from hostel360.hostel_migration.engine import engine_is_running

		if self.status != "Running" or engine_is_running():
			return False
		note = _(
			"The process running this migration stopped before finishing (for example a server restart)."
		)
		self.db_set(
			{"status": "Failed", "log": "\n".join(filter(None, [self.log, note]))}, update_modified=False
		)
		return True

	def assert_can_run(self):
		if not has_any_role(ADMIN_ROLES):
			frappe.throw(_("Only a Hostel Administrator can run a data migration."), frappe.PermissionError)
		self.check_permission("write")

	@frappe.whitelist(methods=["POST"])
	def start(self) -> None:
		"""Queue the run. Progress shows on the form; the report is attached when done."""
		from hostel360.hostel_migration.engine import enqueue_run, resolve_source_dir

		self.assert_can_run()
		if self.status != "Draft":
			frappe.throw(_("This run has already been started. Use Run Again to start a new run."))
		resolve_source_dir(self.source_path, restrict=True)
		self.validate_mapping()
		enqueue_run(self.name)
		self.add_comment(
			"Info", _("Dry run queued") if self.dry_run else _("Migration queued (changes will be kept)")
		)

	@frappe.whitelist(methods=["POST"])
	def run_again(self, dry_run: int | bool = 1) -> str:
		"""Copy this run's source and mapping into a new Draft run."""
		self.assert_can_run()
		if self.status == "Running" and not self.release_if_stale():
			frappe.throw(_("Wait for this run to finish first."))
		copy = frappe.get_doc(
			{
				"doctype": self.doctype,
				"title": self.title,
				"source_path": self.source_path,
				"mapping": self.mapping,
				"entity_filter": self.entity_filter,
				"dry_run": 1 if frappe.utils.cint(dry_run) else 0,
			}
		)
		copy.insert()
		return copy.name
