"""Legacy data migration engine (tender TR 5).

A run reads one CSV/XLSX file per entity from the source folder, maps every row with the
mapping (``mapping.py``) and upserts it into Hostel360:

* **Idempotent.** Each migrated row is remembered in Migration Record Map (entity +
  legacy ID -> record). A second run updates what changed and leaves the rest alone; the
  checksum of the mapped values tells the two apart. Records edited in Hostel360 after
  the migration keep those edits unless the legacy row itself changes.
* **One bad row never stops a run.** Every row runs inside its own savepoint; a failure
  rolls back only that row and is listed in the report with the source line to fix.
* **Historical data.** ``frappe.flags.hostel360_migration`` is set for the whole run, so
  controllers accept past dates, supplied statuses and paid amounts, and send nothing.
* **Dry run.** Everything is validated and written inside one transaction, the
  reconciliation is computed, and then the transaction is rolled back. Only the
  Migration Run with its report is kept.
"""

import json
import os
import time
import traceback
from dataclasses import dataclass, field
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import cint, cstr, now_datetime, strip_html

from hostel360.hostel_migration.attachments import attach_file, source_attachment, stored_file_sha256
from hostel360.hostel_migration.mapping import (
	EntitySpec,
	Mapping,
	MappingError,
	RowError,
	check_against_meta,
	get_cell,
	load_mapping,
)
from hostel360.hostel_migration.sources import missing_columns, read_table
from hostel360.hostel_migration.transforms import TransformError, is_empty, to_text

RUN_DOCTYPE = "Migration Run"
MAP_DOCTYPE = "Migration Record Map"

STATUS_DRAFT = "Draft"
STATUS_RUNNING = "Running"
STATUS_COMPLETED = "Completed"
STATUS_WITH_ERRORS = "Completed with Errors"
STATUS_FAILED = "Failed"

# Real runs commit in batches: small enough that an interrupted run loses little work,
# large enough not to slow a 10,000-row student file down.
COMMIT_EVERY = 100
PROGRESS_EVERY = 50
MAX_LOG_LINES = 4000
MAX_LOGGED_FAILURES = 500
# A dry run is one transaction; a full rehearsal of a large hostel can exceed Frappe's
# default write limit without anything being wrong.
DRY_RUN_MAX_WRITES = 5_000_000
JOB_TIMEOUT = 4 * 60 * 60

ATTACHMENT_VERIFIED = "verified"
ATTACHMENT_DRY_RUN = "checked (dry run)"
ATTACHMENT_CHANGED = "changed in Hostel360 since migration"


class MigrationLocked(frappe.ValidationError):
	pass


@dataclass
class EntityStats:
	entity: str
	target_doctype: str
	source_file: str
	status: str = "Pending"
	source_rows: int = 0
	excluded: int = 0
	created: int = 0
	updated: int = 0
	skipped: int = 0
	failed: int = 0
	target_count: int = 0
	source_checksum: str | None = None
	duration_seconds: float = 0.0
	source_totals: dict = field(default_factory=dict)
	unparsed_totals: dict = field(default_factory=dict)

	def as_row(self) -> dict:
		return {
			"entity": self.entity,
			"target_doctype": self.target_doctype,
			"source_file": self.source_file,
			"status": self.status,
			"source_rows": self.source_rows,
			"excluded": self.excluded,
			"created": self.created,
			"updated": self.updated,
			"skipped": self.skipped,
			"failed": self.failed,
			"target_count": self.target_count,
			"source_checksum": self.source_checksum,
			"duration_seconds": round(self.duration_seconds, 1),
		}


def clean_error(exc: BaseException) -> str:
	"""A one-line, HTML-free message for the report."""
	message = strip_html(cstr(exc)).strip()
	if not message and getattr(frappe.local, "message_log", None):
		last = frappe.local.message_log[-1]
		message = strip_html(cstr(last.get("message") if isinstance(last, dict) else last)).strip()
	if isinstance(exc, RowError | TransformError | frappe.ValidationError):
		return " ".join(message.split()) or exc.__class__.__name__
	return f"{exc.__class__.__name__}: {' '.join(message.split())}"


# ---------------------------------------------------------------------- source folder


def migration_base_dir() -> str:
	return frappe.get_site_path("private", "migration")


def resolve_source_dir(source_path: str, restrict: bool) -> str:
	"""Absolute source folder.

	Runs started from the desk may only read below the site's ``private/migration``
	folder (or folders listed in ``hostel360_migration_sources`` in site_config), so a
	desk user cannot point the engine at arbitrary server files. Bench commands are run by
	the server operator and may read any folder.
	"""
	source_path = (source_path or "").strip()
	if not source_path:
		frappe.throw(_("Enter the source folder."))
	base = migration_base_dir()
	path = source_path if os.path.isabs(source_path) else os.path.join(base, source_path)
	path = os.path.realpath(path)
	if restrict:
		allowed = [os.path.realpath(base)] + [
			os.path.realpath(p) for p in (frappe.conf.get("hostel360_migration_sources") or [])
		]
		if not any(path == root or path.startswith(root + os.sep) for root in allowed):
			frappe.throw(
				_("The source folder must be inside {0}. Copy the legacy export there first.").format(base),
				frappe.PermissionError,
			)
	if not os.path.isdir(path):
		frappe.throw(_("Source folder {0} does not exist on the server.").format(source_path))
	return path


def parse_entity_filter(value) -> list[str]:
	if not value:
		return []
	if isinstance(value, list | tuple):
		items = value
	else:
		items = cstr(value).replace(",", "\n").splitlines()
	return [item.strip() for item in items if item and item.strip()]


# ---------------------------------------------------------------------------- engine


class MigrationEngine:
	def __init__(
		self,
		run_name: str,
		progress=None,
		restrict_source: bool = True,
		commit_every: int = COMMIT_EVERY,
	):
		self.run_name = run_name
		self.progress = progress
		self.restrict_source = restrict_source
		self.commit_every = max(cint(commit_every), 1)

		self.run = frappe.get_doc(RUN_DOCTYPE, run_name)
		self.dry_run = bool(self.run.dry_run)
		self.mapping: Mapping | None = None
		self.source_dir: str | None = None
		self.log_lines: list[str] = []
		self.failures: list[dict] = []
		self.attachments: list[dict] = []
		self.stats: dict[str, EntityStats] = {}
		self.maps: dict[str, dict[str, frappe._dict]] = {}
		self.lookup_cache: dict[tuple, str | None] = {}
		self.savepoint_counter = 0
		self.rows_since_commit = 0

	# ------------------------------------------------------------------ logging

	def log(self, message: str) -> None:
		line = f"[{now_datetime().strftime('%H:%M:%S')}] {message}"
		self.log_lines.append(line)
		if self.progress:
			self.progress(message)

	def record_failure(self, entity: str, line: int | None, source_id: str | None, error: str) -> None:
		self.failures.append({"entity": entity, "line": line, "source_id": source_id, "error": error})
		if len(self.failures) <= MAX_LOGGED_FAILURES:
			where = f"line {line}" if line else "file"
			ident = f" [{source_id}]" if source_id else ""
			self.log(f"  {entity} {where}{ident}: {error}")
		elif len(self.failures) == MAX_LOGGED_FAILURES + 1:
			self.log(_("  Further failures are listed only in the Excel report."))

	# ------------------------------------------------------------------ lifecycle

	def execute(self) -> str:
		lock_name = migration_lock_name()
		acquired = frappe.db.sql("SELECT GET_LOCK(%s, 0)", (lock_name,))[0][0]
		if not cint(acquired):
			frappe.throw(
				_("Another migration run is in progress on this site. Wait for it to finish."), MigrationLocked
			)

		previous_user = frappe.session.user
		previous_flags = {
			"hostel360_migration": frappe.flags.hostel360_migration,
			"mute_emails": frappe.flags.mute_emails,
		}
		previous_max_writes = frappe.db.MAX_WRITES_PER_TRANSACTION
		started = time.monotonic()
		try:
			self.mark_running(previous_user)
			frappe.set_user("Administrator")
			frappe.flags.hostel360_migration = True
			frappe.flags.mute_emails = True
			if self.dry_run:
				frappe.db.MAX_WRITES_PER_TRANSACTION = max(previous_max_writes, DRY_RUN_MAX_WRITES)

			reconciliation = None
			fatal = None
			try:
				self.prepare()
				for entity in self.mapping.ordered_entities(parse_entity_filter(self.run.entity_filter)):
					self.process_entity(entity)
				from hostel360.hostel_migration.reconciliation import reconcile

				reconciliation = reconcile(self)
			except Exception as e:
				fatal = e
				self.log(_("Run stopped: {0}").format(clean_error(e)))
				if not isinstance(e, MappingError | frappe.ValidationError):
					self.log(traceback.format_exc())
					frappe.log_error(title=f"Migration run {self.run_name} failed")

			if self.dry_run or fatal:
				frappe.db.rollback()
				if self.dry_run:
					self.log(_("Dry run: all changes were rolled back."))
				else:
					self.log(
						_(
							"Rows saved before the stop are kept; running the same files again continues from there."
						)
					)
			else:
				frappe.db.commit()  # nosemgrep

			self.log(_("Finished in {0} seconds.").format(round(time.monotonic() - started, 1)))
			return self.finish(reconciliation, fatal)
		finally:
			frappe.flags.hostel360_migration = previous_flags["hostel360_migration"]
			frappe.flags.mute_emails = previous_flags["mute_emails"]
			frappe.db.MAX_WRITES_PER_TRANSACTION = previous_max_writes
			frappe.set_user(previous_user)
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_name,))

	def mark_running(self, user: str) -> None:
		self.run.db_set(
			{
				"status": STATUS_RUNNING,
				"started_on": now_datetime(),
				"finished_on": None,
				"started_by": user,
			},
			update_modified=False,
		)
		frappe.db.commit()  # nosemgrep

	def prepare(self) -> None:
		self.mapping = load_mapping(self.run.mapping)
		self.source_dir = resolve_source_dir(self.run.source_path, self.restrict_source)
		mode = _("dry run") if self.dry_run else _("migration")
		self.log(_("Starting {0} of '{1}' from {2}").format(mode, self.mapping.name, self.source_dir))
		problems = check_against_meta(self.mapping)
		if problems:
			for problem in problems:
				self.log("  " + problem)
			raise MappingError(_("The mapping does not match this site ({0} problems).").format(len(problems)))

	def finish(self, reconciliation: dict | None, fatal: Exception | None) -> str:
		try:
			return self.write_results(reconciliation, fatal)
		except Exception:
			# The run must never be left "Running" because its results could not be saved.
			frappe.db.rollback()
			frappe.log_error(title=f"Migration run {self.run_name}: results could not be saved")
			frappe.db.set_value(
				RUN_DOCTYPE,
				self.run_name,
				{
					"status": STATUS_FAILED,
					"finished_on": now_datetime(),
					"log": "\n".join(self.log_lines[-MAX_LOG_LINES:])
					+ "\n"
					+ _("The results could not be saved; see the Error Log."),
				},
				update_modified=False,
			)
			frappe.db.commit()  # nosemgrep
			return STATUS_FAILED

	def write_results(self, reconciliation: dict | None, fatal: Exception | None) -> str:
		from hostel360.hostel_migration.reconciliation import attach_reports

		run = frappe.get_doc(RUN_DOCTYPE, self.run_name)
		total_failed = sum(s.failed for s in self.stats.values())
		if fatal:
			status = STATUS_FAILED
		elif total_failed or not reconciliation or reconciliation.get("status") != "Matched":
			status = STATUS_WITH_ERRORS
		else:
			status = STATUS_COMPLETED

		run.status = status
		run.finished_on = now_datetime()
		if self.mapping:
			run.mapping_name = self.mapping.name
			run.source_system = self.mapping.source_system
			run.mapping_checksum = self.mapping.checksum
		run.set("entities", [])
		for stats in self.stats.values():
			run.append("entities", stats.as_row())
		run.total_source_rows = sum(s.source_rows for s in self.stats.values())
		run.total_created = sum(s.created for s in self.stats.values())
		run.total_updated = sum(s.updated for s in self.stats.values())
		run.total_skipped = sum(s.skipped for s in self.stats.values())
		run.total_failed = total_failed
		run.attachments_verified = sum(1 for a in self.attachments if a.get("status") == ATTACHMENT_VERIFIED)
		run.reconciliation_status = (reconciliation or {}).get("status") or "Not Run"
		run.reconciliation = frappe.as_json(reconciliation) if reconciliation else None

		lines = self.log_lines
		if len(lines) > MAX_LOG_LINES:
			lines = [*lines[: MAX_LOG_LINES // 2], _("... {0} lines omitted ...").format(len(lines) - MAX_LOG_LINES), *lines[-MAX_LOG_LINES // 2 :]]
		run.log = "\n".join(lines)
		run.flags.ignore_permissions = True
		run.flags.from_engine = True
		run.save()

		if reconciliation:
			try:
				attach_reports(run, reconciliation, self.failures)
			except Exception:
				frappe.log_error(title=f"Migration run {self.run_name}: report could not be written")
		frappe.db.commit()  # nosemgrep
		return status

	# ------------------------------------------------------------------ record map

	def load_maps(self, entity: str) -> dict[str, frappe._dict]:
		if entity not in self.maps:
			rows = frappe.get_all(
				MAP_DOCTYPE,
				filters={"source_system": self.mapping.source_system, "source_entity": entity},
				fields=["name", "source_id", "target_doctype", "target_name", "row_checksum"],
				limit_page_length=0,
			)
			self.maps[entity] = {row.source_id: row for row in rows}
		return self.maps[entity]

	def lookup_entity(self, entity: str, source_id: str) -> str | None:
		record = self.load_maps(entity).get(source_id)
		return record.target_name if record else None

	def save_record(self, entity: EntitySpec, source_id: str, target_doctype: str, target_name: str, checksum: str, line: int):
		records = self.load_maps(entity.name)
		existing = records.get(source_id)
		values = {
			"target_doctype": target_doctype,
			"target_name": target_name,
			"row_checksum": checksum,
			"source_row": line,
			"migration_run": self.run_name,
			"last_synced_on": now_datetime(),
		}
		if existing:
			frappe.db.set_value(MAP_DOCTYPE, existing.name, values)
			existing.update(values)
			return
		doc = frappe.get_doc(
			{
				"doctype": MAP_DOCTYPE,
				"source_system": self.mapping.source_system,
				"source_entity": entity.name,
				"source_id": source_id,
				**values,
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		records[source_id] = frappe._dict(
			name=doc.name,
			source_id=source_id,
			target_doctype=target_doctype,
			target_name=target_name,
			row_checksum=checksum,
		)

	def resolve_lookup(self, spec, value):
		lookup = spec.lookup
		text = to_text(value).strip()
		required = lookup.get("required", True)
		if lookup.get("entity"):
			name = self.lookup_entity(lookup["entity"], text)
			if not name and required:
				raise RowError(
					f"{spec.target}: legacy {lookup['entity']} ID '{text}' has not been migrated (missing or failed earlier)."
				)
			return name
		key = (lookup["doctype"], lookup["field"], text)
		if key not in self.lookup_cache:
			self.lookup_cache[key] = frappe.db.get_value(lookup["doctype"], {lookup["field"]: text}, "name")
		name = self.lookup_cache[key]
		if not name and required:
			raise RowError(f"{spec.target}: no {lookup['doctype']} with {lookup['field']} '{text}'.")
		return name

	# ------------------------------------------------------------------ entities

	def process_entity(self, entity: EntitySpec) -> None:
		stats = EntityStats(entity=entity.name, target_doctype=entity.doctype, source_file=entity.file)
		if entity.mode == "child":
			stats.target_doctype = frappe.get_meta(entity.doctype).get_field(entity.table).options
		self.stats[entity.name] = stats
		started = time.monotonic()
		self.log(_("{0}: reading {1}").format(entity.name, entity.file))

		try:
			table = read_table(self.source_dir, entity.file, entity.sheet, entity.encoding, entity.delimiter)
			missing = missing_columns(table, entity.referenced_columns())
			if missing:
				raise MappingError(
					_("{0} has no column(s) {1}. Columns found: {2}.").format(
						entity.file, ", ".join(missing), ", ".join(table.headers)
					)
				)
		except MappingError as e:
			stats.status = "Failed"
			stats.duration_seconds = time.monotonic() - started
			self.record_failure(entity.name, None, None, clean_error(e))
			stats.failed += 1
			return

		stats.source_checksum = table.checksum
		self.load_maps(entity.name)
		for total in entity.totals:
			stats.source_totals[total.label] = Decimal("0")
			stats.unparsed_totals[total.label] = 0

		seen: set[str] = set()
		count = len(table.rows)
		for position, (line, row) in enumerate(table.rows, start=1):
			stats.source_rows += 1
			if not entity.is_included(row):
				stats.excluded += 1
				continue
			self.add_source_totals(entity, stats, row)

			try:
				source_id = entity.source_id(row)
			except RowError as e:
				stats.failed += 1
				self.record_failure(entity.name, line, None, clean_error(e))
				continue
			if source_id in seen:
				stats.failed += 1
				self.record_failure(entity.name, line, source_id, _("The legacy ID appears more than once in the file."))
				continue
			seen.add(source_id)

			outcome = self.process_row_safely(entity, line, source_id, row)
			if outcome:
				setattr(stats, outcome, getattr(stats, outcome) + 1)
			else:
				stats.failed += 1

			if position % PROGRESS_EVERY == 0 or position == count:
				self.publish_progress(entity, position, count)

		if not self.dry_run:
			frappe.db.commit()  # nosemgrep
			self.rows_since_commit = 0
		stats.status = "Completed with Errors" if stats.failed else "Completed"
		stats.duration_seconds = time.monotonic() - started
		self.log(
			_("{0}: {1} rows, {2} created, {3} updated, {4} unchanged, {5} excluded, {6} failed").format(
				entity.name,
				stats.source_rows,
				stats.created,
				stats.updated,
				stats.skipped,
				stats.excluded,
				stats.failed,
			)
		)

	def add_source_totals(self, entity: EntitySpec, stats: EntityStats, row: dict) -> None:
		for total in entity.totals:
			try:
				value = entity.total_value(total, row)
			except TransformError:
				stats.unparsed_totals[total.label] += 1
				continue
			if value is not None:
				stats.source_totals[total.label] += Decimal(str(value))

	def publish_progress(self, entity: EntitySpec, position: int, count: int) -> None:
		try:
			frappe.publish_progress(
				position * 100 / max(count, 1),
				title=_("Migrating {0}").format(entity.name),
				doctype=RUN_DOCTYPE,
				docname=self.run_name,
				description=_("{0} of {1} rows").format(position, count),
			)
		except Exception:
			# Progress is cosmetic; a missing realtime server must not fail the run.
			return

	def process_row_safely(self, entity: EntitySpec, line: int, source_id: str, row: dict) -> str | None:
		self.savepoint_counter += 1
		savepoint = f"h360_mig_{self.savepoint_counter}"
		frappe.db.savepoint(savepoint)
		pending_attachments: list[dict] = []
		try:
			if entity.mode == "child":
				outcome = self.upsert_child(entity, line, source_id, row)
			else:
				outcome = self.upsert_document(entity, line, source_id, row, pending_attachments)
		except Exception as e:
			# The record map cache is only written by save_record, the last step of a row,
			# so a rolled-back row never leaves a stale entry behind.
			frappe.db.rollback(save_point=savepoint)
			self.record_failure(entity.name, line, source_id, clean_error(e))
			frappe.clear_messages()
			return None

		frappe.db.release_savepoint(savepoint)
		self.attachments.extend(pending_attachments)
		frappe.clear_messages()
		self.rows_since_commit += 1
		if not self.dry_run and self.rows_since_commit >= self.commit_every:
			frappe.db.commit()  # nosemgrep
			self.rows_since_commit = 0
		return outcome

	# ------------------------------------------------------------------ documents

	def find_match(self, entity: EntitySpec, values: dict) -> str | None:
		filters = {fieldname: values.get(fieldname) for fieldname in entity.match_on}
		if any(is_empty(v) for v in filters.values()):
			return None
		return frappe.db.get_value(entity.doctype, filters, "name")

	def upsert_document(self, entity: EntitySpec, line: int, source_id: str, row: dict, pending: list) -> str:
		built = entity.build(row, self.resolve_lookup)

		files = {}
		for fieldname, relative in built.attachments.items():
			path, sha256 = source_attachment(self.source_dir, self.mapping.attachments_dir, relative)
			files[fieldname] = (relative, path, sha256)
		checksum = built.checksum({f: sha for f, (_rel, _path, sha) in files.items()})
		# Frappe stamps new records with the current time; a legacy "created on" (when a
		# grievance was raised, a leave applied for) is written back after saving.
		creation = built.values.pop("creation", None)

		record = self.load_maps(entity.name).get(source_id)
		target = None
		if record and record.target_doctype == entity.doctype and frappe.db.exists(entity.doctype, record.target_name):
			target = record.target_name
		if not target and entity.match_on:
			target = self.find_match(entity, built.values)

		if target and record and record.target_name == target and record.row_checksum == checksum:
			for fieldname, (relative, _path, sha256) in files.items():
				pending.append(self.check_kept_attachment(entity, source_id, target, fieldname, relative, sha256))
			return "skipped"

		if target:
			doc = frappe.get_doc(entity.doctype, target)
			if doc.docstatus == 2:
				raise RowError(
					_("{0} {1} is cancelled in Hostel360; the legacy row was not applied.").format(entity.doctype, target)
				)
			if doc.docstatus == 1:
				if entity.on_submitted_change == "skip":
					if not record:
						self.save_record(entity, source_id, entity.doctype, doc.name, checksum, line)
					return "skipped"
				raise RowError(
					_(
						"{0} {1} is already submitted, so the changed legacy values were not applied. Cancel and amend it, or correct the source row."
					).format(entity.doctype, target)
				)
			self.apply_values(doc, built, is_new=False)
			doc.flags.ignore_permissions = True
			doc.save()
			if entity.submit:
				doc.submit()
			outcome = "updated"
		else:
			doc = frappe.new_doc(entity.doctype)
			self.apply_values(doc, built, is_new=True)
			doc.flags.ignore_permissions = True
			doc.insert(set_name=built.docname or None)
			if entity.submit:
				doc.submit()
			outcome = "created"

		if creation:
			frappe.db.set_value(entity.doctype, doc.name, "creation", creation, update_modified=False)

		for fieldname, (relative, path, sha256) in files.items():
			entry = {
				"entity": entity.name,
				"source_id": source_id,
				"doctype": entity.doctype,
				"name": doc.name,
				"field": fieldname,
				"source_path": relative,
				"source_sha256": sha256,
				"target_file": None,
				"target_sha256": None,
				"status": ATTACHMENT_DRY_RUN,
			}
			if not self.dry_run:
				result = attach_file(doc, fieldname, path, sha256)
				entry.update(target_file=result["file_url"], target_sha256=result["target_sha256"], status=result["status"])
			pending.append(entry)

		self.save_record(entity, source_id, entity.doctype, doc.name, checksum, line)
		return outcome

	def check_kept_attachment(self, entity, source_id, name, fieldname, relative, sha256) -> dict:
		"""An unchanged row's attachment: is the stored file still the legacy file?"""
		file_url = frappe.db.get_value(entity.doctype, name, fieldname)
		stored = stored_file_sha256(file_url)
		return {
			"entity": entity.name,
			"source_id": source_id,
			"doctype": entity.doctype,
			"name": name,
			"field": fieldname,
			"source_path": relative,
			"source_sha256": sha256,
			"target_file": file_url,
			"target_sha256": stored,
			# A photo replaced in Hostel360 after go-live is a later edit, not a migration fault.
			"status": ATTACHMENT_VERIFIED if stored == sha256 else ATTACHMENT_CHANGED,
		}

	def apply_values(self, doc, built, is_new: bool) -> None:
		# Empty legacy cells never clear a value that is already in Hostel360: a blank in
		# a later extract usually means "not exported", not "erase".
		for fieldname, value in built.values.items():
			if value is None:
				continue
			doc.set(fieldname, value)
		for table, rows in built.children.items():
			doc.set(table, [])
			for values in rows:
				doc.append(table, {k: v for k, v in values.items() if v is not None})

	def upsert_child(self, entity: EntitySpec, line: int, source_id: str, row: dict) -> str:
		built = entity.build(row, self.resolve_lookup)
		parent_id = to_text(get_cell(row, entity.parent["column"])).strip()
		if not parent_id:
			raise RowError(_("{0} is empty.").format(entity.parent["column"]))
		parent_name = self.lookup_entity(entity.parent["entity"], parent_id)
		if not parent_name:
			raise RowError(
				_("Parent {0} '{1}' has not been migrated (missing or failed earlier).").format(
					entity.parent["entity"], parent_id
				)
			)

		child_doctype = frappe.get_meta(entity.doctype).get_field(entity.table).options
		checksum = built.checksum()
		record = self.load_maps(entity.name).get(source_id)
		if record and record.row_checksum == checksum and frappe.db.exists(child_doctype, record.target_name):
			return "skipped"

		parent = frappe.get_doc(entity.doctype, parent_name)
		if parent.docstatus != 0:
			raise RowError(_("{0} {1} is submitted; its rows cannot be changed.").format(entity.doctype, parent_name))

		rows = parent.get(entity.table) or []
		child = None
		if record:
			child = next((r for r in rows if r.name == record.target_name), None)
		if not child and entity.match_field:
			wanted = built.values.get(entity.match_field)
			child = next((r for r in rows if wanted is not None and r.get(entity.match_field) == wanted), None)

		values = {k: v for k, v in built.values.items() if v is not None}
		if child:
			child.update(values)
			outcome = "updated"
		else:
			child = parent.append(entity.table, values)
			outcome = "created"
		parent.flags.ignore_permissions = True
		parent.save()
		self.save_record(entity, source_id, child_doctype, child.name, checksum, line)
		return outcome


# ------------------------------------------------------------------------ entry points


def migration_lock_name() -> str:
	"""MariaDB named lock held while a run executes; released even if the process dies."""
	return f"h360_migration_{frappe.conf.db_name}"[:64]


def engine_is_running() -> bool:
	return frappe.db.sql("SELECT IS_USED_LOCK(%s)", (migration_lock_name(),))[0][0] is not None


def create_run(
	source_path: str,
	mapping,
	dry_run: bool = True,
	entities=None,
	title: str | None = None,
) -> str:
	"""Create a Draft Migration Run. ``mapping`` is JSON text, a dict, or a file path."""
	parsed = load_mapping(mapping)
	run = frappe.get_doc(
		{
			"doctype": RUN_DOCTYPE,
			"title": title or (_("Dry run") if dry_run else _("Migration")) + f": {parsed.name}",
			"source_path": source_path,
			# Keep the author's key order; frappe.as_json would sort it.
			"mapping": json.dumps(parsed.raw, indent=1, ensure_ascii=False),
			"mapping_name": parsed.name,
			"source_system": parsed.source_system,
			"dry_run": 1 if dry_run else 0,
			"entity_filter": "\n".join(parse_entity_filter(entities)),
		}
	)
	run.flags.ignore_permissions = True
	run.insert()
	frappe.db.commit()  # nosemgrep
	return run.name


def execute_run(run: str, restrict_source: bool = True, progress=None) -> str:
	"""Run a Migration Run now and return its final status."""
	return MigrationEngine(run, progress=progress, restrict_source=restrict_source).execute()


def run_in_background(run: str) -> None:
	"""Background job entry point (queue ``long``)."""
	status = frappe.db.get_value(RUN_DOCTYPE, run, "status")
	if status == STATUS_RUNNING:
		return
	try:
		execute_run(run, restrict_source=True)
	except MigrationLocked:
		frappe.db.set_value(RUN_DOCTYPE, run, "status", STATUS_DRAFT)
		frappe.db.set_value(
			RUN_DOCTYPE, run, "log", _("Another migration was running; start this run again when it has finished.")
		)
		frappe.db.commit()  # nosemgrep


def enqueue_run(run: str) -> None:
	frappe.enqueue(
		"hostel360.hostel_migration.engine.run_in_background",
		queue="long",
		timeout=JOB_TIMEOUT,
		job_id=f"hostel360-migration-{run}",
		deduplicate=True,
		enqueue_after_commit=True,
		run=run,
	)
