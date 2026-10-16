# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import hashlib

import frappe
from frappe import _
from frappe.model.document import Document

# Map keys longer than this are hashed so the unique index stays within its column.
MAX_PLAIN_KEY_LENGTH = 200


def make_map_key(source_system: str, source_entity: str, source_id: str) -> str:
	"""Unique key for a legacy row: ``acgcet-register/students:2021ME014``.

	The source system keeps two legacy applications (or a rehearsal and a test) apart even
	when both have a "students" table with a row whose ID is 1. Very long legacy
	identifiers (some systems use composite keys) are replaced by their SHA-256 so the key
	fits the indexed column.
	"""
	source_system = (source_system or "").strip()
	source_entity = (source_entity or "").strip()
	source_id = str(source_id if source_id is not None else "").strip()
	key = f"{source_system}/{source_entity}:{source_id}"
	if len(key) <= MAX_PLAIN_KEY_LENGTH:
		return key
	digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()
	return f"{source_system[:60]}/{source_entity[:60]}:#{digest}"


class MigrationRecordMap(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		last_synced_on: DF.Datetime | None
		map_key: DF.Data | None
		migration_run: DF.Link | None
		row_checksum: DF.Data | None
		source_entity: DF.Data
		source_id: DF.Data
		source_row: DF.Int
		source_system: DF.Data
		target_doctype: DF.Link
		target_name: DF.DynamicLink
	# end: auto-generated types

	def validate(self):
		self.source_system = (self.source_system or "").strip()
		self.source_entity = (self.source_entity or "").strip()
		self.source_id = str(self.source_id or "").strip()
		if not (self.source_system and self.source_entity and self.source_id):
			frappe.throw(_("Source system, entity and ID are all required."))
		self.map_key = make_map_key(self.source_system, self.source_entity, self.source_id)
