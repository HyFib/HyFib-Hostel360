# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""The site's own record of its exit exports.

``h360-export`` writes a row when an export is finished and ``h360-verify-export`` (run with
``--site``) marks it verified. The deletion after exit (``h360-purge-institution-data``)
only proceeds on a recent, verified, complete export, and stamps the row with the
acknowledgement reference and certificate. The rows hold no student or parent data, so the
deletion keeps them as the site's audit trail of the exit.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, now_datetime

SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_PROBLEMS_STORED = 50


class ExitExportLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		acknowledgement_reference: DF.Data | None
		archive_path: DF.SmallText | None
		archive_sha256: DF.Data | None
		certificate_id: DF.Data | None
		directory_path: DF.SmallText | None
		doctype_count: DF.Int
		encrypted: DF.Check
		entry_count: DF.Int
		export_copy_deleted: DF.Check
		export_name: DF.Data
		exported_by: DF.Data | None
		exported_on: DF.Datetime
		include_files: DF.Check
		manifest_sha256: DF.Data
		purged_on: DF.Datetime | None
		recorded_from: DF.Literal["Export", "Verification"]
		site: DF.Data
		status: DF.Literal["Exported", "Verified", "Verification Failed"]
		total_bytes: DF.Int
		verification_problems: DF.SmallText | None
		verified_by: DF.Data | None
		verified_on: DF.Datetime | None
		verified_path: DF.SmallText | None
	# end: auto-generated types

	def validate(self):
		self.export_name = (self.export_name or "").strip()
		if not self.export_name:
			frappe.throw(_("The export name is required."))
		for fieldname in ("manifest_sha256", "archive_sha256"):
			value = (self.get(fieldname) or "").strip().lower()
			if value and not SHA256.match(value):
				frappe.throw(
					_("{0} must be a SHA-256 checksum (64 hexadecimal characters).").format(
						self.meta.get_label(fieldname)
					)
				)
			self.set(fieldname, value or None)
		if not self.manifest_sha256:
			frappe.throw(_("The manifest checksum is required."))


def record_export(result: dict, user: str | None = None) -> str:
	"""Log a finished export (the dict returned by ``run_exit_export``)."""
	doc = frappe.get_doc(
		{
			"doctype": "Exit Export Log",
			"export_name": result["name"],
			"status": "Exported",
			"site": frappe.local.site,
			"exported_on": get_datetime(result.get("created_on"))
			if result.get("created_on")
			else now_datetime(),
			"exported_by": user or frappe.session.user,
			"recorded_from": "Export",
			"include_files": cint(result.get("include_files")),
			"encrypted": cint(result.get("encrypted")),
			"doctype_count": cint(result.get("doctypes")),
			"entry_count": cint(result.get("entries")),
			"total_bytes": cint(result.get("bytes")),
			"archive_path": result.get("archive"),
			"directory_path": result.get("directory"),
			"manifest_sha256": result["manifest_sha256"],
			"archive_sha256": result.get("archive_sha256"),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def record_verification(result, site: str | None = None, user: str | None = None) -> tuple[str | None, str]:
	"""Record an ``h360-verify-export`` outcome against this site's export log.

	``result`` is a ``verify.VerifyResult``. Only an export of this site is recorded. An
	export with no log row (made before exports were logged) is adopted when it verifies.
	A copy whose manifest differs from the one written at export time is never treated as
	verified, even if its files match that different manifest.

	Returns the log name (or None) and a sentence for the operator.
	"""
	site = site or frappe.local.site
	user = user or frappe.session.user
	if not result.export_name or not result.manifest_sha256:
		return None, _("Nothing recorded: no readable manifest was found.")
	if result.site != site:
		return None, _("Nothing recorded on {0}: this export was made on {1}.").format(
			site, result.site or "?"
		)

	problems = list(result.problems)
	name = frappe.db.get_value("Exit Export Log", {"export_name": result.export_name}, "name")
	if name:
		doc = frappe.get_doc("Exit Export Log", name, for_update=True)
		if doc.site != site:
			return None, _("Nothing recorded: log {0} belongs to {1}.").format(doc.name, doc.site)
		if doc.manifest_sha256 != result.manifest_sha256:
			problems.insert(0, _("manifest.json differs from the manifest written when the export was made."))
	elif result.ok:
		summary = result.manifest_summary or {}
		doc = frappe.get_doc(
			{
				"doctype": "Exit Export Log",
				"export_name": result.export_name,
				"site": site,
				"exported_on": get_datetime(result.created_on) if result.created_on else now_datetime(),
				"exported_by": summary.get("created_by"),
				"recorded_from": "Verification",
				"include_files": cint(summary.get("include_files")),
				"encrypted": cint(result.encrypted),
				"doctype_count": cint(summary.get("doctypes")),
				"entry_count": cint(summary.get("entries")),
				"total_bytes": cint(summary.get("bytes")),
				"archive_path": result.path if result.archive_checksum else None,
				"directory_path": None if result.archive_checksum else result.path,
				"manifest_sha256": result.manifest_sha256,
				"archive_sha256": result.archive_checksum,
			}
		)
	else:
		return None, _(
			"Nothing recorded: the export failed verification and is not in this site's export log."
		)

	doc.status = "Verification Failed" if problems else "Verified"
	doc.verified_on = now_datetime()
	doc.verified_by = user
	doc.verified_path = result.path
	shown = problems[:MAX_PROBLEMS_STORED]
	if len(problems) > len(shown):
		shown.append(_("... and {0} more").format(len(problems) - len(shown)))
	doc.verification_problems = "\n".join(shown) or None
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	if problems:
		return doc.name, _("Recorded on {0}: export {1} FAILED verification.").format(site, doc.name)
	return doc.name, _("Recorded on {0}: export {1} is verified.").format(site, doc.name)


def last_verified_export(site: str | None = None) -> frappe._dict | None:
	"""The newest export of this site that passed verification, or None."""
	rows = frappe.get_all(
		"Exit Export Log",
		filters={"site": site or frappe.local.site, "status": "Verified"},
		fields=[
			"name",
			"export_name",
			"exported_on",
			"exported_by",
			"include_files",
			"encrypted",
			"doctype_count",
			"entry_count",
			"total_bytes",
			"archive_path",
			"directory_path",
			"manifest_sha256",
			"archive_sha256",
			"verified_on",
			"verified_by",
			"verified_path",
		],
		order_by="exported_on desc, creation desc",
		limit=1,
	)
	return rows[0] if rows else None
