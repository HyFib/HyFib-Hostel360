# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from hostel360.hostel_mess.utils import get_institution, in_migration


class MessBill(Document):
	"""One student's share of a Mess Bill Run. Created and changed only by the run."""

	def before_insert(self):
		if not (self.flags.from_bill_run or in_migration()):
			frappe.throw(_("Mess bills are created by computing a Mess Bill Run."))

	def validate(self):
		if not self.is_new() and not in_migration():
			frappe.throw(_("Mess bills cannot be edited. Recompute the bill run instead."))
		self.chargeable_days = max((self.enrolled_days or 0) - (self.reduction_days or 0), 0)

	def on_trash(self):
		if self.status != "Draft":
			frappe.throw(_("A published mess bill cannot be deleted. Cancel its bill run instead."))

	def onload(self):
		if self.fee_invoice and frappe.db.table_exists("Hostel Fee Invoice"):
			invoice = frappe.db.get_value(
				"Hostel Fee Invoice",
				self.fee_invoice,
				["status", "outstanding_amount", "due_date"],
				as_dict=True,
			)
			if invoice:
				self.set_onload("invoice", invoice)

	def get_print_context(self) -> dict:
		"""Everything the Mess Bill print format needs, gathered defensively."""
		run = (
			frappe.db.get_value(
				"Mess Bill Run",
				self.mess_bill_run,
				[
					"provision_cost",
					"other_expenses",
					"other_credits",
					"total_cost",
					"total_student_days",
					"daily_rate",
					"due_date",
					"status",
				],
				as_dict=True,
			)
			or frappe._dict()
		)
		extras = frappe.get_all(
			"Mess Extra Charge",
			filters={"mess_bill": self.name},
			fields=["charge_date", "charge_type", "description", "amount"],
			order_by="charge_date asc",
		)
		if not extras and self.status == "Draft":
			extras = frappe.get_all(
				"Mess Extra Charge",
				filters={
					"student": self.student,
					"mess": self.mess,
					"status": "Unbilled",
					"charge_date": ["between", [self.from_date, self.to_date]],
				},
				fields=["charge_date", "charge_type", "description", "amount"],
				order_by="charge_date asc",
			)
		student = frappe._dict()
		if frappe.db.table_exists("Hostel Student"):
			student = (
				frappe.db.get_value(
					"Hostel Student",
					self.student,
					["register_number", "roll_number", "program", "year_of_study", "current_room"],
					as_dict=True,
				)
				or frappe._dict()
			)
		return {
			"run": run,
			"extras": extras,
			"student": student,
			"institution": get_institution(),
			"unrounded_total": flt(flt(self.mess_charge) + flt(self.extras_amount), 2),
		}
