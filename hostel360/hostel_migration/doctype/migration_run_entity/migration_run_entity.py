# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MigrationRunEntity(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		created: DF.Int
		duration_seconds: DF.Float
		entity: DF.Data
		excluded: DF.Int
		failed: DF.Int
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		skipped: DF.Int
		source_checksum: DF.Data | None
		source_file: DF.Data | None
		source_rows: DF.Int
		status: DF.Literal["Pending", "Completed", "Completed with Errors", "Failed", "Not Run"]
		target_count: DF.Int
		target_doctype: DF.Link | None
		updated: DF.Int
	# end: auto-generated types

	pass
