"""Deleting the institution's data after exit (tender TR 22).

At the end of the contract HyFib hands the institution a complete export (``h360-export``),
the institution confirms in writing that it has received it, and HyFib then deletes the
institution's data and issues a deletion certificate. This module is the application's
part of that deletion. It removes

* every record of every Hostel360 DocType, child tables and settings included, with their
  encrypted secrets; the Exit Export Log alone is kept (it holds checksums and file
  locations, no student or parent data, and is the site's own record of the exit);
* the Frappe records that point at those records: files, versions (audit trail), comments,
  communications, activity, access and view logs, notifications, assignments, shares,
  tags, e-mail queue, error logs, deleted-document copies, prepared reports, data imports
  and search-index rows;
* the user accounts of students, parents and institution staff (users linked from a
  student, guardian or staff record, or holding an institution role), with their roles,
  permissions, sessions, contacts, saved settings and login history. Frappe's standard
  users and HyFib operations accounts are not institution data and stay;
* uploaded files, the site's local backups, the legacy extracts kept for migration and
  this server's copy of the exit export.

Safeguards: the site name must be given twice, the reference of the institution's written
confirmation is required, the default is a dry run that only counts, and the deletion is
refused unless the newest verified export of the site is at most 30 days old and includes
the uploaded files. ``force_without_export`` overrides only that last check, and the
certificate says so.

Stores outside the application (the database server, off-site and volume backups, logs
kept under the CERT-In directions) are listed on the certificate as still to be destroyed
with the exit runbook.
"""

import datetime
import functools
import getpass
import glob
import json
import operator
import os
import re
import shutil
import socket
import unicodedata
from urllib.parse import unquote

import frappe
from frappe import _
from frappe.query_builder.functions import Count
from frappe.utils import add_days, cint, get_bench_path, get_datetime, get_system_timezone, now_datetime

from hostel360.hostel_migration import certificate
from hostel360.hostel_migration.doctype.exit_export_log.exit_export_log import last_verified_export
from hostel360.hostel_migration.export import APP, app_doctypes
from hostel360.setup.roles import (
	ADMIN_ROLE,
	AUDITOR_ROLE,
	DEPUTY_WARDEN_ROLE,
	MESS_MANAGER_ROLE,
	MESS_STAFF_ROLE,
	OFFICE_ROLE,
	OPERATIONS_ROLE,
	PARENT_ROLE,
	STUDENT_ROLE,
	WARDEN_ROLE,
)

EXPORT_MAX_AGE_DAYS = 30
CERT_IN_LOG_RETENTION_DAYS = 180
REFERENCE_MIN_LENGTH = 3
REFERENCE_MAX_LENGTH = 140
DEFAULT_CERTIFICATE_FOLDER = "hostel360-exit"
ISSUER = "HyFib Solutions Private Limited"

# Kept: the site's record of its exports and of this deletion.
RETAINED_DOCTYPES = frozenset({"Exit Export Log"})
INSTITUTION_ROLES = frozenset(
	{
		ADMIN_ROLE,
		WARDEN_ROLE,
		DEPUTY_WARDEN_ROLE,
		OFFICE_ROLE,
		MESS_MANAGER_ROLE,
		MESS_STAFF_ROLE,
		AUDITOR_ROLE,
		STUDENT_ROLE,
		PARENT_ROLE,
	}
)
STANDARD_USERS = frozenset({"Administrator", "Guest"})
# Records whose user account is created by Hostel360 for the person they describe.
PEOPLE_DOCTYPES = (("Hostel Student", "student"), ("Hostel Guardian", "parent"), ("Hostel Staff", "staff"))

# Frappe records that point at another record by (DocType, name). The name column may be
# None when only the DocType is recorded.
REFERENCE_COLUMNS = (
	("Version", "ref_doctype", "docname"),
	("Comment", "reference_doctype", "reference_name"),
	("Activity Log", "reference_doctype", "reference_name"),
	("Activity Log", "timeline_doctype", "timeline_name"),
	("Activity Log", "link_doctype", "link_name"),
	("View Log", "reference_doctype", "reference_name"),
	("Access Log", "export_from", "reference_document"),
	("Notification Log", "document_type", "document_name"),
	("Notification Log", "source_doctype", "source_name"),
	("ToDo", "reference_type", "reference_name"),
	("DocShare", "share_doctype", "share_name"),
	("Document Follow", "ref_doctype", "ref_docname"),
	("Tag Link", "document_type", "document_name"),
	("Error Log", "reference_doctype", "reference_name"),
	("Deleted Document", "deleted_doctype", "deleted_name"),
	("Submission Queue", "ref_doctype", "ref_docname"),
	("Integration Request", "reference_doctype", "reference_docname"),
	("Webhook Request Log", "reference_doctype", "reference_document"),
	("Document Share Key", "reference_doctype", "reference_docname"),
	("Email Unsubscribe", "reference_doctype", "reference_name"),
	("Reminder", "reminder_doctype", "reminder_docname"),
	("Workflow Action", "reference_doctype", "reference_name"),
	("Auto Repeat", "reference_doctype", "reference_document"),
	("Event", "reference_doctype", "reference_docname"),
	("Event Participants", "reference_doctype", "reference_docname"),
	("Dynamic Link", "link_doctype", "link_name"),
	("Energy Point Log", "reference_doctype", "reference_name"),
)

# Frappe log rows that belong to a user (by login or by e-mail address).
USER_COLUMNS = (
	("Activity Log", "user"),
	("Access Log", "user"),
	("View Log", "viewed_by"),
	("Route History", "user"),
	("Notification Log", "for_user"),
	("Notification Log", "from_user"),
	("ToDo", "allocated_to"),
	("DocShare", "user"),
	("Document Follow", "user"),
	("Reminder", "user"),
	("User Permission", "user"),
	("Workflow Action", "user"),
	("Webhook Request Log", "user"),
	("API Request Log", "user"),
	("List Filter", "for_user"),
	("Token Cache", "user"),
	("OAuth Authorization Code", "user"),
	("OAuth Bearer Token", "user"),
	("Comment", "comment_email"),
)

# Whole documents that belong to a user, by the column naming the login or e-mail address.
PERSONAL_DOCUMENTS = (
	("Personal Data Download Request", "user"),
	("Personal Data Deletion Request", "email"),
	("Custom Sidebar", "user"),
	("Dock", "user"),
)

# Whole documents removed by name (with their child rows), in this order. Users go last so
# every record that is selected through a user is selected before the account goes.
PARTIAL_DOCTYPES = (
	"Email Queue",
	"Communication",
	"Prepared Report",
	"Data Import",
	"Personal Data Download Request",
	"Personal Data Deletion Request",
	"Custom Sidebar",
	"Dock",
	"Notification Settings",
	"Contact",
	"User",
)
# Records that hang off a removed document through a plain Link rather than a child table.
DEPENDENTS = {"Data Import": (("Data Import Log", "data_import"),)}

# Folders under public/files that Frappe writes itself (theme stylesheets); they are not uploads.
FRAMEWORK_FILE_FOLDERS = ("website_theme",)

GROUP_HOSTEL360 = "hostel360_doctypes"
GROUP_FRAPPE = "frappe_records"
GROUP_INTERNAL = "internal_tables"


class PurgeRefused(frappe.ValidationError):
	pass


def clean_reference(reference: str | None) -> str:
	"""The institution's confirmation reference, on one line, checked."""
	text = " ".join(str(reference or "").split())
	if len(text) < REFERENCE_MIN_LENGTH:
		raise PurgeRefused(
			_(
				"Give the reference of the institution's written confirmation that it received the export "
				"(at least {0} characters)."
			).format(REFERENCE_MIN_LENGTH)
		)
	if len(text) > REFERENCE_MAX_LENGTH:
		raise PurgeRefused(
			_("The acknowledgement reference may be at most {0} characters long.").format(
				REFERENCE_MAX_LENGTH
			)
		)
	if any(unicodedata.category(ch).startswith("C") for ch in text):
		raise PurgeRefused(_("The acknowledgement reference contains control characters."))
	return text


def _iso(value) -> str | None:
	if not value:
		return None
	return get_datetime(value).isoformat(sep=" ", timespec="seconds")


def _system_user() -> str:
	try:
		return getpass.getuser()
	except Exception:
		return str(os.getuid()) if hasattr(os, "getuid") else "unknown"


def _any(criteria: list):
	return functools.reduce(operator.or_, criteria)


class _Op:
	"""Rows of one table selected for deletion (``where`` None selects every row)."""

	__slots__ = ("group", "label", "table", "where")

	def __init__(self, group: str, label: str, table, where=None):
		self.group, self.label, self.table, self.where = group, label, table, where

	def _filtered(self, query):
		return query.where(self.where) if self.where is not None else query

	def count(self) -> int:
		return cint(self._filtered(frappe.qb.from_(self.table).select(Count("*"))).run()[0][0])

	def delete(self) -> int:
		rows = self.count()
		if rows:
			self._filtered(frappe.qb.from_(self.table).delete()).run()
		return rows


class InstitutionDataPurge:
	"""Plan (count) or carry out the deletion of the institution's data on this site.

	``site_path`` and ``maintenance`` exist for tests: the files, backups and migration
	folders are looked for under ``site_path`` (default: the site's own folder), and with
	``maintenance`` the site is put into maintenance mode with its scheduler paused before
	anything is deleted (it stays that way: the site is being retired).
	"""

	def __init__(
		self,
		confirm_site: str,
		acknowledgement_reference: str,
		*,
		force_without_export: bool = False,
		keep_export_copy: bool = False,
		certificate_dir: str | None = None,
		site_path: str | None = None,
		maintenance: bool = True,
		progress=None,
	):
		self.site = frappe.local.site
		if (confirm_site or "").strip() != self.site:
			raise PurgeRefused(
				_("The site to confirm ({0}) is not this site ({1}); nothing was done.").format(
					confirm_site, self.site
				)
			)
		self.reference = clean_reference(acknowledgement_reference)
		self.force_without_export = bool(force_without_export)
		self.keep_export_copy = bool(keep_export_copy)
		self.site_path = os.path.realpath(site_path or frappe.get_site_path())
		self.certificate_dir = os.path.realpath(
			certificate_dir or os.path.join(get_bench_path(), DEFAULT_CERTIFICATE_FOLDER)
		)
		self.maintenance = maintenance
		self.progress = progress
		self.scope: frappe._dict | None = None

	def say(self, message: str) -> None:
		if self.progress:
			self.progress(message)

	# --------------------------------------------------------------- safeguards

	def check(self) -> frappe._dict:
		"""Check the preconditions; raise PurgeRefused when the deletion may not go ahead."""
		sites_folder = os.path.dirname(self.site_path)
		if _inside(self.certificate_dir, self.site_path) or _inside(
			self.certificate_dir, os.path.join(sites_folder, "assets")
		):
			raise PurgeRefused(
				_(
					"Write the certificate outside the site's folder ({0}); that folder is removed when the site is dropped."
				).format(self.site_path)
			)

		now = now_datetime()
		export = last_verified_export(self.site)
		problem = None
		if not export:
			problem = _("No verified exit export is on record for this site.")
		else:
			exported_on = get_datetime(export.exported_on)
			age_days = (now - exported_on).days
			export.age_days = age_days
			if now - exported_on > datetime.timedelta(days=EXPORT_MAX_AGE_DAYS):
				problem = _(
					"The newest verified exit export, {0}, was made on {1}, {2} days ago; it may be at most {3} days old."
				).format(export.export_name, _iso(exported_on), age_days, EXPORT_MAX_AGE_DAYS)
			elif not cint(export.include_files):
				problem = _(
					"The newest verified exit export, {0}, was made without the uploaded files."
				).format(export.export_name)
		if problem and not self.force_without_export:
			raise PurgeRefused(
				problem
				+ " "
				+ _(
					"Make a complete export with h360-export, verify it on this site with "
					"'bench --site {0} h360-verify-export --path ...', and run this again "
					"(or pass --force-without-export, which the certificate records)."
				).format(self.site)
			)

		warnings = []
		if export:
			newer = frappe.get_all(
				"Exit Export Log",
				filters={
					"site": self.site,
					"status": ["!=", "Verified"],
					"exported_on": [">", export.exported_on],
				},
				fields=["name", "status"],
				order_by="exported_on desc",
			)
			warnings += [
				_("A newer export, {0}, is not verified ({1}).").format(row.name, _(row.status))
				for row in newer
			]
		return frappe._dict(
			export=export, problem=problem, forced=bool(problem), warnings=warnings, checked_on=now
		)

	# ------------------------------------------------------------------ scope

	def collect(self) -> frappe._dict:
		"""Decide what is deleted. Must run before anything is deleted."""
		doctypes = [d for d in app_doctypes() if d.name not in RETAINED_DOCTYPES]
		purged = sorted(d.name for d in doctypes)
		purged_set = set(purged)
		modules = sorted(set(frappe.get_module_list(APP)) | {d.module for d in doctypes})

		# Frappe child tables used inside Hostel360 documents keep rows of ours.
		foreign_children: dict[str, set] = {}
		for d in doctypes:
			if d.istable or d.issingle:
				continue
			for df in frappe.get_meta(d.name).get_table_fields():
				if df.options not in purged_set:
					foreign_children.setdefault(df.options, set()).add(d.name)

		users = self._collect_users(purged_set)
		user_names = sorted(users)
		emails = set(user_names)
		if user_names:
			emails |= set(self._pluck("User", "email", frappe.qb.DocType("User").name.isin(user_names)))
		emails.discard(None)
		emails.discard("")
		emails = sorted(emails)

		partial: dict[str, set] = {name: set() for name in PARTIAL_DOCTYPES}
		partial["User"] = set(user_names)
		partial["Notification Settings"] = set(user_names) if self._table("Notification Settings") else set()
		people_keys = sorted(set(user_names) | set(emails))
		for doctype, column in PERSONAL_DOCUMENTS:
			if people_keys and self._has_columns(doctype, column):
				partial[doctype] = self._pluck(
					doctype, "name", frappe.qb.DocType(doctype)[column].isin(people_keys)
				)

		if self._table("Contact"):
			contact = frappe.qb.DocType("Contact")
			criteria = []
			if user_names:
				criteria.append(contact.user.isin(user_names))
			if emails:
				criteria.append(contact.email_id.isin(emails))
			if criteria:
				partial["Contact"] = self._pluck("Contact", "name", _any(criteria))
			if emails and self._has_columns("Contact Email", "email_id", "parent"):
				contact_email = frappe.qb.DocType("Contact Email")
				partial["Contact"] |= self._pluck(
					"Contact Email",
					"parent",
					(contact_email.email_id.isin(emails)) & (contact_email.parenttype == "Contact"),
				)

		people = {"User": partial["User"], "Contact": partial["Contact"]}
		if self._table("Communication"):
			communication = frappe.qb.DocType("Communication")
			criteria = [communication.reference_doctype.isin(purged)]
			for doctype, names in people.items():
				if names:
					criteria.append(
						(communication.reference_doctype == doctype)
						& communication.reference_name.isin(sorted(names))
					)
			if user_names:
				criteria.append(communication.user.isin(user_names))
			if emails:
				criteria.append(communication.sender.isin(emails))
			found = self._pluck("Communication", "name", _any(criteria))
			if self._table("Communication Link"):
				link = frappe.qb.DocType("Communication Link")
				criteria = [link.link_doctype.isin(purged)]
				for doctype, names in people.items():
					if names:
						criteria.append((link.link_doctype == doctype) & link.link_name.isin(sorted(names)))
				found |= self._pluck("Communication Link", "parent", _any(criteria))
			partial["Communication"] = found

		reports = sorted(frappe.get_all("Report", filters={"module": ["in", modules]}, pluck="name"))
		if self._table("Prepared Report"):
			if reports:
				partial["Prepared Report"] = self._pluck(
					"Prepared Report", "name", frappe.qb.DocType("Prepared Report").report_name.isin(reports)
				)
		if self._table("Data Import"):
			partial["Data Import"] = self._pluck(
				"Data Import", "name", frappe.qb.DocType("Data Import").reference_doctype.isin(purged)
			)

		if self._table("Email Queue"):
			queue = frappe.qb.DocType("Email Queue")
			criteria = [
				self._reference_criterion(queue, "reference_doctype", "reference_name", purged, partial)
			]
			if partial["Communication"]:
				criteria.append(queue.communication.isin(sorted(partial["Communication"])))
			found = self._pluck("Email Queue", "name", _any(criteria))
			if emails and self._table("Email Queue Recipient"):
				recipient = frappe.qb.DocType("Email Queue Recipient")
				found |= self._pluck("Email Queue Recipient", "parent", recipient.recipient.isin(emails))
			partial["Email Queue"] = found

		self.scope = frappe._dict(
			doctypes=doctypes,
			purged=purged,
			modules=modules,
			reports=reports,
			foreign_children={k: sorted(v) for k, v in foreign_children.items()},
			users=users,
			emails=emails,
			partial={k: v for k, v in partial.items() if v},
		)
		return self.scope

	def _collect_users(self, purged: set) -> dict[str, str]:
		"""Accounts of the institution's people: login -> kind."""
		kinds: dict[str, str] = {}
		for doctype, kind in PEOPLE_DOCTYPES:
			if doctype in purged and self._has_columns(doctype, "user"):
				table = frappe.qb.DocType(doctype)
				for user in sorted(self._pluck(doctype, "user", table.user.isnotnull() & (table.user != ""))):
					kinds.setdefault(user, kind)
		has_role = frappe.qb.DocType("Has Role")
		hyfib = self._pluck(
			"Has Role", "parent", (has_role.parenttype == "User") & (has_role.role == OPERATIONS_ROLE)
		)
		role_holders = self._pluck(
			"Has Role",
			"parent",
			(has_role.parenttype == "User") & has_role.role.isin(sorted(INSTITUTION_ROLES)),
		)
		for user in sorted(role_holders - hyfib):
			kinds.setdefault(user, "institution staff")
		candidates = sorted(set(kinds) - STANDARD_USERS)
		existing = (
			self._pluck("User", "name", frappe.qb.DocType("User").name.isin(candidates))
			if candidates
			else set()
		)
		return {user: kinds[user] for user in sorted(existing)}

	# ---------------------------------------------------------------- helpers

	def _table(self, doctype: str) -> bool:
		return bool(frappe.db.get_db_table_columns(f"tab{doctype}"))

	def _has_columns(self, doctype: str, *columns: str) -> bool:
		existing = set(frappe.db.get_db_table_columns(f"tab{doctype}"))
		return bool(existing) and all(c in existing for c in columns)

	def _raw_has_columns(self, table: str, *columns: str) -> bool:
		existing = set(frappe.db.get_db_table_columns(table))
		return bool(existing) and all(c in existing for c in columns)

	def _pluck(self, doctype: str, column: str, where) -> set:
		table = frappe.qb.DocType(doctype)
		return {
			row[0]
			for row in frappe.qb.from_(table).select(table[column]).distinct().where(where).run()
			if row[0]
		}

	@staticmethod
	def _reference_criterion(table, dt_col: str, name_col: str | None, purged: list, partial: dict):
		criteria = [table[dt_col].isin(purged)]
		if name_col:
			for doctype, names in sorted(partial.items()):
				if names:
					criteria.append((table[dt_col] == doctype) & table[name_col].isin(sorted(names)))
		return _any(criteria)

	def _file_criterion(self):
		s = self.scope
		file = frappe.qb.DocType("File")
		# Uploads attached to nothing (portal uploads never linked to a record, files dropped
		# into the file manager) are the institution's as well on a site that only it uses.
		unattached = (file.attached_to_doctype.isnull() | (file.attached_to_doctype == "")) & (
			file.is_folder == 0
		)
		return (
			self._reference_criterion(file, "attached_to_doctype", "attached_to_name", s.purged, s.partial)
			| unattached
		)

	def operations(self) -> list[_Op]:
		"""One delete per table, so counts are exact and the plan equals the execution."""
		s = self.scope or self.collect()
		entries: dict[str, dict] = {}

		def add(group: str, label: str, table, where=None, everything: bool = False):
			entry = entries.setdefault(label, {"group": group, "table": table, "criteria": [], "all": False})
			if everything:
				entry["all"] = True
			elif where is not None:
				entry["criteria"].append(where)

		file = frappe.qb.DocType("File")
		add(GROUP_FRAPPE, "File", file, self._file_criterion())

		for doctype, dt_col, name_col in REFERENCE_COLUMNS:
			if not self._has_columns(doctype, dt_col, *((name_col,) if name_col else ())):
				continue
			table = frappe.qb.DocType(doctype)
			add(
				GROUP_FRAPPE,
				doctype,
				table,
				self._reference_criterion(table, dt_col, name_col, s.purged, s.partial),
			)

		people_keys = sorted(set(s.users) | set(s.emails))
		if people_keys:
			for doctype, column in USER_COLUMNS:
				if self._has_columns(doctype, column):
					table = frappe.qb.DocType(doctype)
					add(GROUP_FRAPPE, doctype, table, table[column].isin(people_keys))

		# Exports of Hostel360 reports, and the desk pages staff opened on its records (the
		# route names the record, e.g. "Form/Hostel Student/2021ME014").
		if s.reports and self._has_columns("Access Log", "report_name"):
			access = frappe.qb.DocType("Access Log")
			add(GROUP_FRAPPE, "Access Log", access, access.report_name.isin(s.reports))
		# Tracebacks carry the local variables of the code that failed, so errors raised in
		# Hostel360 code can hold names and numbers even without a reference to a record.
		if self._has_columns("Error Log", "method", "error"):
			error = frappe.qb.DocType("Error Log")
			add(
				GROUP_FRAPPE,
				"Error Log",
				error,
				error.method.like("%hostel360%") | error.error.like("%hostel360%"),
			)
		if self._has_columns("Scheduled Job Log", "details"):
			job_log = frappe.qb.DocType("Scheduled Job Log")
			add(GROUP_FRAPPE, "Scheduled Job Log", job_log, job_log.details.like("%hostel360%"))
		# Inbound mail the site could not file; on the institution's own site it is the
		# institution's correspondence.
		if self._table("Unhandled Email"):
			add(GROUP_FRAPPE, "Unhandled Email", frappe.qb.DocType("Unhandled Email"), everything=True)
		if self._has_columns("Route History", "route"):
			route = frappe.qb.DocType("Route History")
			add(
				GROUP_FRAPPE,
				"Route History",
				route,
				_any(
					[
						route.route.like(f"%/{doctype}") | route.route.like(f"%/{doctype}/%")
						for doctype in s.purged
					]
				),
			)

		auth = frappe.qb.Table("__Auth")
		search = frappe.qb.Table("__global_search")
		has_auth = self._raw_has_columns("__Auth", "doctype", "name")
		has_search = self._raw_has_columns("__global_search", "doctype", "name")
		for doctype in PARTIAL_DOCTYPES:
			names = sorted(s.partial.get(doctype) or ())
			if not names or not self._table(doctype):
				continue
			for df in frappe.get_meta(doctype).get_table_fields():
				if self._has_columns(df.options, "parent", "parenttype"):
					child = frappe.qb.DocType(df.options)
					add(
						GROUP_FRAPPE,
						df.options,
						child,
						(child.parenttype == doctype) & child.parent.isin(names),
					)
			for dependent, link_field in DEPENDENTS.get(doctype, ()):
				if self._has_columns(dependent, link_field):
					table = frappe.qb.DocType(dependent)
					add(GROUP_FRAPPE, dependent, table, table[link_field].isin(names))
			table = frappe.qb.DocType(doctype)
			add(GROUP_FRAPPE, doctype, table, table.name.isin(names))
			if has_auth:
				add(GROUP_INTERNAL, "__Auth", auth, (auth.doctype == doctype) & auth.name.isin(names))
			if has_search:
				add(
					GROUP_INTERNAL,
					"__global_search",
					search,
					(search.doctype == doctype) & search.name.isin(names),
				)

		for d in s.doctypes:
			if d.issingle:
				singles = frappe.qb.DocType("Singles")
				add(GROUP_HOSTEL360, d.name, singles, singles.doctype == d.name)
			elif self._table(d.name):
				add(GROUP_HOSTEL360, d.name, frappe.qb.DocType(d.name), everything=True)
		for child, parents in sorted(s.foreign_children.items()):
			if self._has_columns(child, "parenttype"):
				table = frappe.qb.DocType(child)
				add(GROUP_FRAPPE, child, table, table.parenttype.isin(parents))

		if has_auth:
			add(GROUP_INTERNAL, "__Auth", auth, auth.doctype.isin(s.purged))
		if has_search:
			add(GROUP_INTERNAL, "__global_search", search, search.doctype.isin(s.purged))
		if self._raw_has_columns("__UserSettings", "user", "doctype"):
			settings = frappe.qb.Table("__UserSettings")
			add(GROUP_INTERNAL, "__UserSettings", settings, settings.doctype.isin(s.purged))
			if s.users:
				add(GROUP_INTERNAL, "__UserSettings", settings, settings.user.isin(sorted(s.users)))
		if s.users:
			defaults = frappe.qb.DocType("DefaultValue")
			# Two-factor secrets are stored as site defaults named after the user.
			otp_keys = sorted(f"{user}_{suffix}" for user in s.users for suffix in ("otpsecret", "otplogin"))
			add(
				GROUP_FRAPPE,
				"DefaultValue",
				defaults,
				defaults.parent.isin(sorted(s.users)) | defaults.defkey.isin(otp_keys),
			)
			if self._has_columns("Sessions", "user"):
				sessions = frappe.qb.DocType("Sessions")
				add(GROUP_INTERNAL, "tabSessions", sessions, sessions.user.isin(sorted(s.users)))

		ops = []
		for label, entry in entries.items():
			if entry["all"]:
				ops.append(_Op(entry["group"], label, entry["table"]))
			elif entry["criteria"]:
				ops.append(_Op(entry["group"], label, entry["table"], _any(entry["criteria"])))
		return ops

	# ------------------------------------------------------------------- plan

	def plan(self, checks: frappe._dict | None = None) -> dict:
		"""What would be deleted, counted; nothing is changed."""
		self.collect()
		counts = {op.label: op.count() for op in self.operations()}
		files = self._sweep_files(dry_run=True, purged_urls=self._purged_file_urls())
		backups = self._empty_folder(self._backups_path(), True)
		migration = self._empty_folder(self._migration_path(), True)
		export = (checks or frappe._dict()).get("export")
		return {
			"site": self.site,
			"acknowledgement_reference": self.reference,
			"deleted": self._summarise(counts, files, backups, migration, self._export_copies(dry_run=True)),
			"changed_since_export": self._changed_since(export.exported_on) if export else [],
			"retained_users": sorted(
				set(frappe.get_all("User", pluck="name")) - set(self.scope.users) - {"Guest"}
			),
			"certificate_dir": self.certificate_dir,
		}

	def _summarise(
		self, counts: dict, files: dict, backups: dict, migration: dict, export_copies: list
	) -> dict:
		s = self.scope
		groups = {GROUP_FRAPPE: [], GROUP_INTERNAL: []}
		labels = {op.label: op.group for op in self.operations()}
		doctype_rows = []
		for d in sorted(s.doctypes, key=lambda d: (d.module, d.name)):
			kind = "settings" if d.issingle else "child table" if d.istable else "records"
			doctype_rows.append(
				{"doctype": d.name, "module": d.module, "kind": kind, "rows": counts.get(d.name, 0)}
			)
		for label, rows in sorted(counts.items()):
			group = labels.get(label)
			if group == GROUP_FRAPPE and rows:
				groups[GROUP_FRAPPE].append({"doctype": label, "rows": rows})
			elif group == GROUP_INTERNAL and rows:
				groups[GROUP_INTERNAL].append({"table": label, "rows": rows})
		by_kind: dict[str, int] = {}
		for kind in s.users.values():
			by_kind[kind] = by_kind.get(kind, 0) + 1
		return {
			GROUP_HOSTEL360: doctype_rows,
			GROUP_FRAPPE: groups[GROUP_FRAPPE],
			GROUP_INTERNAL: groups[GROUP_INTERNAL],
			"users": {"count": len(s.users), "by_kind": by_kind},
			"files": {k: v for k, v in files.items() if k != "failed"},
			"local_backups": {k: v for k, v in backups.items() if k != "failed"},
			"migration_sources": {k: v for k, v in migration.items() if k != "failed"},
			"export_copies": export_copies,
		}

	def _changed_since(self, since) -> list[dict]:
		"""Records added or changed after the export was made (they are not in it)."""
		rows = []
		for d in self.scope.doctypes:
			if d.issingle or d.istable or not self._has_columns(d.name, "modified"):
				continue
			table = frappe.qb.DocType(d.name)
			count = cint(frappe.qb.from_(table).select(Count("*")).where(table.modified > since).run()[0][0])
			if count:
				rows.append({"doctype": d.name, "rows": count})
		return sorted(rows, key=lambda r: (-r["rows"], r["doctype"]))

	# ---------------------------------------------------------------- execute

	def execute(self, commit: bool = True) -> dict:
		"""Delete, verify, and issue the signed certificate. ``commit=False`` is for tests."""
		checks = self.check()
		started = now_datetime()
		certificate_id = (
			f"H360-DEL-{re.sub(r'[^A-Za-z0-9]+', '-', self.site).strip('-')}-{started:%Y%m%d-%H%M%S}"
		)
		institution = frappe.db.get_single_value("Hostel Settings", "institution_name")
		os.makedirs(self.certificate_dir, mode=0o700, exist_ok=True)
		interrupted = self._interrupted_runs()

		self.say(_("Counting what will be deleted"))
		plan = self.plan(checks)
		pending_path = os.path.join(self.certificate_dir, f"{certificate_id}.pending.json")
		certificate.write_private(
			pending_path,
			json.dumps(
				{
					"certificate_id": certificate_id,
					"site": self.site,
					"acknowledgement_reference": self.reference,
					"started_at": _iso(started),
					"planned": plan["deleted"],
				},
				indent=1,
				default=str,
			).encode("utf-8"),
		)

		site_state = {}
		if self.maintenance:
			site_state = self._enter_maintenance()

		self.say(_("Deleting records"))
		purged_urls = self._purged_file_urls()
		counts = {}
		try:
			for op in self.operations():
				counts[op.label] = counts.get(op.label, 0) + op.delete()
			if commit:
				frappe.db.commit()
		except Exception:
			if commit:
				# Rolled back: nothing was deleted, so this run leaves no trace to report later.
				frappe.db.rollback()
				if os.path.exists(pending_path):
					os.remove(pending_path)
			raise

		self.say(_("Deleting files, local backups and legacy extracts"))
		files = self._sweep_files(dry_run=False, purged_urls=purged_urls)
		backups = self._empty_folder(self._backups_path(), False)
		migration = self._empty_folder(self._migration_path(), False)
		export_copies = self._export_copies(dry_run=self.keep_export_copy)

		self.say(_("Checking that nothing selected remains"))
		residual = self._residual(files, backups, migration, export_copies)
		finished = now_datetime()
		deleted = self._summarise(counts, files, backups, migration, export_copies)
		totals = {
			"records": sum(counts.values()),
			"files": files["deleted"]
			+ backups["files"]
			+ migration["files"]
			+ sum(1 for c in export_copies if c["result"] == "deleted"),
			"bytes": files["bytes"]
			+ backups["bytes"]
			+ migration["bytes"]
			+ sum(c.get("bytes", 0) for c in export_copies if c["result"] == "deleted"),
		}
		export = checks.export
		body = {
			"format": certificate.FORMAT,
			"format_version": certificate.FORMAT_VERSION,
			"certificate_id": certificate_id,
			"issued_by": ISSUER,
			"site": self.site,
			"institution": institution,
			"acknowledgement_reference": self.reference,
			"started_at": _iso(started),
			"finished_at": _iso(finished),
			"time_zone": get_system_timezone(),
			"operator": {
				"system_user": _system_user(),
				"host": socket.gethostname(),
				"frappe_user": frappe.session.user,
			},
			"application": {"hostel360": _app_version("hostel360"), "frappe": frappe.__version__},
			"export": self._export_evidence(export) if export else None,
			"forced_without_export": checks.forced,
			"export_problem": checks.problem,
			"warnings": checks.warnings,
			"site_state": site_state,
			"deleted": deleted,
			"totals": totals,
			"changed_since_export": plan["changed_since_export"],
			"verification": {"clean": not residual, "residual": residual},
			"retained": self._retained(),
			"outstanding": self._outstanding(finished),
			"interrupted_runs": [run["summary"] for run in interrupted],
		}
		document = certificate.sign(body, certificate.signing_key())
		paths = certificate.write(document, self.certificate_dir)

		copy_results: dict[str, list[str]] = {}
		for copy in export_copies:
			copy_results.setdefault(copy["log"], []).append(copy["result"])
		deleted_copies = {
			log
			for log, results in copy_results.items()
			if all(r in ("deleted", "not found") for r in results)
		}
		for name in frappe.get_all("Exit Export Log", filters={"site": self.site}, pluck="name"):
			frappe.db.set_value(
				"Exit Export Log",
				name,
				{
					"acknowledgement_reference": self.reference,
					"purged_on": finished,
					"certificate_id": certificate_id,
					"export_copy_deleted": 1 if name in deleted_copies else 0,
				},
			)
		if commit:
			frappe.db.commit()
			frappe.clear_cache()

		for path in [pending_path] + [run["path"] for run in interrupted]:
			if os.path.exists(path):
				os.remove(path)
		return {
			"certificate_id": certificate_id,
			"certificate": document,
			"paths": paths,
			"clean": not residual,
			"residual": residual,
			"totals": totals,
		}

	# ---------------------------------------------------------------- pieces

	def _export_evidence(self, export: frappe._dict) -> dict:
		return {
			"log": export.name,
			"export_name": export.export_name,
			"exported_on": _iso(export.exported_on),
			"exported_by": export.exported_by,
			"age_days": export.get("age_days"),
			"verified_on": _iso(export.verified_on),
			"verified_by": export.verified_by,
			"include_files": bool(cint(export.include_files)),
			"encrypted": bool(cint(export.encrypted)),
			"doctype_count": cint(export.doctype_count),
			"entry_count": cint(export.entry_count),
			"total_bytes": cint(export.total_bytes),
			"manifest_sha256": export.manifest_sha256,
			"archive_sha256": export.archive_sha256,
		}

	def _enter_maintenance(self) -> dict:
		from frappe.installer import update_site_config

		# Nothing may write new records while they are being deleted: no web requests, no
		# scheduled jobs. The site is being retired, so this is not undone.
		update_site_config("maintenance_mode", 1)
		update_site_config("pause_scheduler", 1)
		return {"maintenance_mode": 1, "scheduler_paused": 1}

	def _purged_file_urls(self) -> list[tuple[str, str]]:
		file = frappe.qb.DocType("File")
		rows = (
			frappe.qb.from_(file)
			.select(file.file_url, file.thumbnail_url)
			.where(self._file_criterion())
			.run()
		)
		return [(row[0], row[1]) for row in rows]

	def _file_roots(self) -> dict[str, str]:
		return {
			"/private/files/": os.path.join(self.site_path, "private", "files"),
			"/files/": os.path.join(self.site_path, "public", "files"),
		}

	def _path_for(self, url: str | None) -> str | None:
		"""Disk path of a /files/ or /private/files/ URL, never outside those folders."""
		if not url:
			return None
		for prefix, root in self._file_roots().items():
			if url.startswith(prefix):
				path = os.path.normpath(os.path.join(root, url[len(prefix) :]))
				return path if path.startswith(root + os.sep) else None
		return None

	def _kept_paths(self) -> set[str]:
		"""Files that records staying on the site still use."""
		file = frappe.qb.DocType("File")
		purged = {
			row[0] for row in frappe.qb.from_(file).select(file.name).where(self._file_criterion()).run()
		}
		kept = set()
		for name, url, thumbnail in (
			frappe.qb.from_(file).select(file.name, file.file_url, file.thumbnail_url).run()
		):
			if name in purged:
				continue
			for value in (url, thumbnail):
				for candidate in {value, unquote(value or "")}:
					path = self._path_for(candidate)
					if path:
						kept.add(path)
		return kept

	def _sweep_files(self, dry_run: bool, purged_urls: list[tuple[str, str]]) -> dict:
		"""Delete every uploaded file no remaining record uses (orphans included)."""
		kept = self._kept_paths()
		purged_paths = set()
		for url, thumbnail in purged_urls:
			for value in (url, thumbnail):
				for candidate in {value, unquote(value or "")}:
					path = self._path_for(candidate)
					if path:
						purged_paths.add(path)
		protected = {
			os.path.join(self.site_path, "public", "files", folder) for folder in FRAMEWORK_FILE_FOLDERS
		}
		stats = {
			"records": len(purged_urls),
			"deleted": 0,
			"bytes": 0,
			"orphans": 0,
			"kept": 0,
			"missing_on_disk": 0,
			"failed": [],
		}
		seen = set()
		for root in self._file_roots().values():
			if not os.path.isdir(root):
				continue
			for folder, dirnames, filenames in os.walk(root):
				dirnames[:] = [d for d in dirnames if os.path.join(folder, d) not in protected]
				for filename in filenames:
					path = os.path.normpath(os.path.join(folder, filename))
					seen.add(path)
					if path in kept:
						stats["kept"] += 1
						continue
					size = os.lstat(path).st_size
					if not dry_run:
						try:
							os.remove(path)
						except OSError as e:
							stats["failed"].append(f"{path}: {e.strerror or e}")
							continue
					stats["deleted"] += 1
					stats["bytes"] += size
					if path not in purged_paths:
						stats["orphans"] += 1
			if not dry_run:
				_remove_empty_folders(root, protected)
		stats["missing_on_disk"] = len({p for p in purged_paths if p not in seen and p not in kept})
		return stats

	def _backups_path(self) -> str:
		return os.path.join(self.site_path, "private", "backups")

	def _migration_path(self) -> str:
		return os.path.join(self.site_path, "private", "migration")

	@staticmethod
	def _empty_folder(path: str, dry_run: bool) -> dict:
		"""Delete what is inside ``path`` (the folder itself stays)."""
		stats = {"path": path, "files": 0, "bytes": 0, "failed": []}
		if not os.path.isdir(path):
			return stats
		for folder, _dirs, filenames in os.walk(path):
			for filename in filenames:
				stats["files"] += 1
				stats["bytes"] += os.lstat(os.path.join(folder, filename)).st_size
		if dry_run:
			return stats
		for entry in os.scandir(path):
			try:
				if entry.is_dir(follow_symlinks=False):
					shutil.rmtree(entry.path)
				else:
					os.remove(entry.path)
			except OSError as e:
				stats["failed"].append(f"{entry.path}: {e.strerror or e}")
		return stats

	def _export_copies(self, dry_run: bool) -> list[dict]:
		"""This server's copies of the exit exports (archive, checksum file, folder)."""
		copies = []
		rows = frappe.get_all(
			"Exit Export Log",
			filters={"site": self.site},
			fields=["name", "archive_path", "directory_path"],
			order_by="creation asc",
		)
		for row in rows:
			candidates = []
			if row.archive_path:
				candidates += [row.archive_path, row.archive_path + ".sha256"]
			if row.directory_path:
				candidates.append(row.directory_path)
			for path in candidates:
				entry = {"log": row.name, "path": path, "bytes": 0}
				name = os.path.basename(os.path.normpath(path))
				if not os.path.isabs(path) or not name.startswith("hostel360-export-"):
					# Only paths written by h360-export are ever removed from here.
					entry["result"] = "skipped: not an export path"
				elif not os.path.lexists(path):
					entry["result"] = "not found"
				else:
					entry["bytes"] = _tree_size(path)
					if dry_run:
						entry["result"] = "kept" if self.keep_export_copy else "to delete"
					else:
						try:
							if os.path.isdir(path) and not os.path.islink(path):
								shutil.rmtree(path)
							else:
								os.remove(path)
							entry["result"] = "deleted"
						except OSError as e:
							entry["result"] = f"failed: {e.strerror or e}"
				copies.append(entry)
		return copies

	def _residual(self, files: dict, backups: dict, migration: dict, export_copies: list) -> list[dict]:
		residual = []
		for op in self.operations():
			remaining = op.count()
			if remaining:
				residual.append({"item": f"{op.label} rows", "remaining": remaining})
		left = self._sweep_files(dry_run=True, purged_urls=[])
		if left["deleted"]:
			residual.append({"item": "Uploaded files no remaining record uses", "remaining": left["deleted"]})
		for label, stats in (("Local backups", backups), ("Legacy extracts", migration)):
			after = self._empty_folder(stats["path"], True)
			if after["files"]:
				residual.append({"item": f"{label} ({stats['path']})", "remaining": after["files"]})
		for problem in files["failed"] + backups["failed"] + migration["failed"]:
			residual.append({"item": problem, "remaining": "could not be deleted"})
		for copy in export_copies:
			if copy["result"].startswith("failed"):
				residual.append({"item": f"Export copy {copy['path']}", "remaining": copy["result"]})
		return residual

	def _retained(self) -> list[dict]:
		retained = []
		logs = frappe.db.count("Exit Export Log", {"site": self.site})
		if logs:
			retained.append(
				{
					"item": f"Exit Export Log ({logs} rows)",
					"reason": "The site's record of the exit export and of this deletion: names, checksums and file "
					"locations, no student or parent data. Removed with the site database.",
				}
			)
		users = sorted(set(frappe.get_all("User", pluck="name")) - {"Guest"})
		if users:
			shown = ", ".join(users[:20]) + (f" and {len(users) - 20} more" if len(users) > 20 else "")
			retained.append(
				{
					"item": f"User accounts ({len(users)}): {shown}",
					"reason": "Frappe's standard accounts and HyFib operations accounts; not institution data.",
				}
			)
		file = frappe.qb.DocType("File")
		kept_files = (
			frappe.qb.from_(file)
			.select(file.attached_to_doctype, Count("*"))
			.where(
				(file.is_folder == 0)
				& file.attached_to_doctype.isnotnull()
				& (file.attached_to_doctype != "")
			)
			.groupby(file.attached_to_doctype)
			.run()
		)
		for doctype, count in kept_files:
			retained.append(
				{
					"item": f"{count} file(s) attached to {doctype}",
					"reason": "Site configuration outside Hostel360 data.",
				}
			)
		if self.keep_export_copy:
			retained.append(
				{
					"item": "Server copy of the exit export",
					"reason": "Kept on request (--keep-export-copy); delete it by hand.",
				}
			)
		retained.append(
			{
				"item": f"Application logs in {os.path.join(self.site_path, 'logs')}",
				"reason": f"Kept for {CERT_IN_LOG_RETENTION_DAYS} days under the CERT-In directions of 28 April 2022; "
				"they contain masked identifiers only and expire on their own.",
			}
		)
		return retained

	def _outstanding(self, finished) -> list[str]:
		bench = get_bench_path()
		logs_until = add_days(finished, CERT_IN_LOG_RETENTION_DAYS).date().isoformat()
		return [
			f"Drop the site database and folder without taking a backup: `bench drop-site {self.site} --no-backup "
			f"--force`, then delete `{bench}/archived/sites/{self.site}*` (drop-site moves the folder there).",
			"Delete every restic snapshot of this server and every object version and unfinished multipart upload "
			"in the off-site backup bucket (ap-mumbai-1).",
			"Delete the block-volume and boot-volume backups of the application server in both regions, including "
			"cross-region copies and the ad-hoc pre-deploy and pre-restore snapshots, which never expire once the "
			"server is gone.",
			"Delete the database dumps kept before deployments (/srv/hostel360/backups/pre-deploy) and the restic "
			"cache on the server.",
			"Delete the passphrase file used with h360-export --passphrase-file and any copy of the export kept "
			"outside this server.",
			"Remove the site's Redis data (cache and job queues) with the containers.",
			f"Logs kept under the CERT-In directions (OCI Logging, load balancer, host security and site logs) "
			f"expire by {logs_until}; record their expiry, do not delete them early.",
		]

	def _interrupted_runs(self) -> list[dict]:
		"""Earlier runs on this site that started deleting but issued no certificate."""
		runs = []
		for path in sorted(glob.glob(os.path.join(self.certificate_dir, "*.pending.json"))):
			try:
				with open(path, encoding="utf-8") as f:
					data = json.load(f)
			except (OSError, ValueError):
				continue
			if data.get("site") != self.site:
				continue
			planned = data.get("planned") or {}
			runs.append(
				{
					"path": path,
					"summary": {
						"certificate_id": data.get("certificate_id"),
						"started_at": data.get("started_at"),
						"acknowledgement_reference": data.get("acknowledgement_reference"),
						"planned_records": sum(
							row.get("rows", 0)
							for group in (GROUP_HOSTEL360, GROUP_FRAPPE, GROUP_INTERNAL)
							for row in planned.get(group) or []
						),
						"planned_users": (planned.get("users") or {}).get("count", 0),
						"planned_files": (planned.get("files") or {}).get("deleted", 0),
					},
				}
			)
		return runs


def _inside(path: str, root: str) -> bool:
	path, root = os.path.realpath(path), os.path.realpath(root)
	return path == root or path.startswith(root + os.sep)


def _tree_size(path: str) -> int:
	if os.path.isdir(path) and not os.path.islink(path):
		return sum(
			os.lstat(os.path.join(folder, name)).st_size
			for folder, _dirs, names in os.walk(path)
			for name in names
		)
	return os.lstat(path).st_size


def _remove_empty_folders(root: str, protected: set[str]) -> None:
	for folder, _dirs, _files in os.walk(root, topdown=False):
		if folder == root or any(folder == p or folder.startswith(p + os.sep) for p in protected):
			continue
		try:
			os.rmdir(folder)
		except OSError:
			pass


def _app_version(app: str) -> str | None:
	try:
		return frappe.get_attr(f"{app}.__version__")
	except Exception:
		return None
