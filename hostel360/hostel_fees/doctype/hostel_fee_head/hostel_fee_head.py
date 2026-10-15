# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from hostel360.hostel_fees.constants import SYSTEM_FEE_HEADS


class HostelFeeHead(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
		fee_category: DF.Literal["Hostel", "Mess", "Deposit", "Fine", "Other"]
		fee_head_name: DF.Data
		is_active: DF.Check
		is_refundable: DF.Check
	# end: auto-generated types

	def validate(self):
		self.fee_head_name = (self.fee_head_name or "").strip()
		if self.name in SYSTEM_FEE_HEADS and not self.is_active:
			frappe.throw(
				_("{0} is used by mess billing and fines, so it cannot be deactivated.").format(
					frappe.bold(self.name)
				)
			)

	def on_trash(self):
		if self.name in SYSTEM_FEE_HEADS:
			frappe.throw(
				_("{0} is used by mess billing and fines, so it cannot be deleted.").format(
					frappe.bold(self.name)
				)
			)
