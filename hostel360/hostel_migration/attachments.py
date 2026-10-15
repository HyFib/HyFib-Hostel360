"""Legacy attachments (student photos, scanned forms) copied into private Files.

Every copied file is read back from the site's storage and its SHA-256 compared with the
legacy file, so the reconciliation report can state that each document arrived intact.
"""

import os

import frappe
from frappe import _
from frappe.utils import cint

from hostel360.hostel_migration.mapping import MappingError, RowError
from hostel360.hostel_migration.sources import resolve_inside, sha256_file

MB = 1024 * 1024
# Used when System Settings has no limit; the File DocType enforces the configured one.
DEFAULT_MAX_FILE_MB = 10


def max_attachment_bytes() -> int:
	return (cint(frappe.get_system_settings("max_file_size")) or DEFAULT_MAX_FILE_MB) * MB


def source_attachment(source_dir: str, attachments_dir: str, relative: str) -> tuple[str, str]:
	"""Absolute path and SHA-256 of a legacy attachment, or RowError if it is missing."""
	relative = (relative or "").strip().replace("\\", "/")
	try:
		path = resolve_inside(os.path.join(source_dir, attachments_dir), relative)
	except MappingError as e:
		raise RowError(str(e)) from e
	if not os.path.isfile(path):
		raise RowError(f"Attachment {relative} was not found in the {attachments_dir} folder.")
	size = os.path.getsize(path)
	limit = max_attachment_bytes()
	if size > limit:
		raise RowError(
			f"Attachment {relative} is {size / MB:.1f} MB; the site accepts files up to {limit // MB} MB."
		)
	return path, sha256_file(path)


def stored_file_sha256(file_url: str | None) -> str | None:
	"""SHA-256 of a File's bytes as stored on this site, or None if it cannot be read.

	The bytes are read from disk directly: ``File.get_content`` decodes anything that
	happens to be valid Windows-1252 into text, which would change what is hashed.
	"""
	if not file_url:
		return None
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		return None
	try:
		path = frappe.get_doc("File", name).get_full_path()
		return sha256_file(path)
	except (OSError, frappe.ValidationError):
		return None


def attach_file(doc, fieldname: str, path: str, expected_sha256: str) -> dict:
	"""Store ``path`` as a private File on ``doc.fieldname`` and verify it.

	Returns ``{file_url, target_sha256, status}``; status is "verified" when the stored
	bytes match. If the field already points at a file with the same content, that file
	is reused rather than stored twice.
	"""
	current = doc.get(fieldname)
	if current and stored_file_sha256(current) == expected_sha256:
		return {"file_url": current, "target_sha256": expected_sha256, "status": "verified"}

	with open(path, "rb") as f:
		content = f.read()

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": os.path.basename(path),
			"content": content,
			"is_private": 1,
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"attached_to_field": fieldname,
		}
	)
	file_doc.flags.ignore_permissions = True
	file_doc.insert()

	target_sha256 = stored_file_sha256(file_doc.file_url)
	status = "verified" if target_sha256 == expected_sha256 else "mismatch"
	if status != "verified":
		raise RowError(
			_("Attachment {0} was stored but its checksum does not match the source file.").format(
				os.path.basename(path)
			)
		)
	doc.db_set(fieldname, file_doc.file_url, update_modified=False)
	return {"file_url": file_doc.file_url, "target_sha256": target_sha256, "status": status}
