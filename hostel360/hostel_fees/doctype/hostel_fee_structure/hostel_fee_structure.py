# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, now_datetime, nowdate
from frappe.utils.background_jobs import is_job_enqueued

from hostel360.hostel_fees.constants import FEE_HEAD, INVOICE, STRUCTURE
from hostel360.hostel_fees.utils import get_permitted_hostels, money

PUBLISH_QUEUE = "long"
PUBLISH_TIMEOUT = 60 * 60
COMMIT_EVERY = 50


def publish_job_id(structure: str) -> str:
	return f"hostel360:publish_dues:{structure}"


class HostelFeeStructure(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from hostel360.hostel_fees.doctype.hostel_fee_structure_item.hostel_fee_structure_item import (
			HostelFeeStructureItem,
		)

		academic_year: DF.Link
		due_date: DF.Date
		hostel: DF.Link | None
		invoices_created: DF.Int
		is_active: DF.Check
		items: DF.Table[HostelFeeStructureItem]
		last_published_by: DF.Link | None
		last_published_on: DF.Datetime | None
		notes: DF.SmallText | None
		program: DF.Link | None
		publish_log: DF.SmallText | None
		publish_status: DF.Literal[
			"Not Published", "Queued", "In Progress", "Completed", "Completed with Errors", "Failed"
		]
		structure_name: DF.Data
		total_amount: DF.Currency
		year_of_study: DF.Literal["", "1", "2", "3", "4", "5"]
	# end: auto-generated types

	def validate(self):
		self.structure_name = (self.structure_name or "").strip()
		self.validate_items()
		self.total_amount = money(sum(money(row.amount) for row in self.items))
		self.warn_about_published_invoices()

	def validate_items(self):
		if not self.items:
			frappe.throw(_("Add at least one fee component."))

		seen = set()
		for row in self.items:
			row.amount = money(row.amount)
			if row.amount <= 0:
				frappe.throw(
					_("Row {0}: amount for {1} must be greater than zero.").format(row.idx, row.fee_head)
				)
			if row.fee_head in seen:
				frappe.throw(
					_("Row {0}: {1} appears more than once. Combine the amounts into one row.").format(
						row.idx, frappe.bold(row.fee_head)
					)
				)
			seen.add(row.fee_head)

		inactive = frappe.get_all(
			FEE_HEAD, filters={"name": ("in", list(seen)), "is_active": 0}, pluck="name"
		)
		if inactive:
			frappe.throw(_("Fee heads {0} are inactive.").format(", ".join(inactive)))

	def warn_about_published_invoices(self):
		if self.is_new() or not self.has_value_changed("total_amount"):
			return
		if frappe.db.exists(INVOICE, {"fee_structure": self.name, "docstatus": 1}):
			frappe.msgprint(
				_(
					"Invoices already published from this structure keep their original amounts. "
					"Publishing again only invoices students who have not been invoiced yet."
				),
				indicator="orange",
				alert=True,
			)

	def before_print(self, settings=None):
		parts = [
			(frappe.db.get_value("Hostel", self.hostel, "hostel_name") or self.hostel)
			if self.hostel
			else _("All hostels"),
			_("Year {0}").format(self.year_of_study) if self.year_of_study else _("all years"),
			self.program or _("all programmes"),
		]
		self.print_applicability = ", ".join(parts)
		refundable = set(
			frappe.get_all(
				FEE_HEAD,
				filters={"name": ("in", [row.fee_head for row in self.items]), "is_refundable": 1},
				pluck="name",
			)
		)
		for row in self.items:
			row.is_refundable = row.fee_head in refundable
		if self.get("for_student") and self.for_student.get("current_hostel"):
			self.for_student.hostel_name = frappe.db.get_value(
				"Hostel", self.for_student.current_hostel, "hostel_name"
			)

	# -- publishing --------------------------------------------------------------------------

	def get_matching_students(self, user: str | None = None) -> list[str]:
		"""Active, validated students the structure applies to.

		Staff restricted to some hostels (User Permissions) only ever bill students of those
		hostels, even from a structure that applies to all hostels.
		"""
		filters = {"status": "Active", "validation_status": "Validated"}
		permitted = get_permitted_hostels(INVOICE, user)
		if self.hostel:
			if permitted is not None and self.hostel not in permitted:
				return []
			filters["current_hostel"] = self.hostel
		elif permitted is not None:
			filters["current_hostel"] = ("in", permitted)
		if self.year_of_study:
			filters["year_of_study"] = self.year_of_study
		if self.program:
			filters["program"] = self.program
		return frappe.get_all("Hostel Student", filters=filters, pluck="name", order_by="name asc")

	def get_invoiced_students(self, reissue_cancelled: bool = False) -> set[str]:
		"""Students who already have an invoice from this structure.

		A cancelled invoice counts too, so a fee waived by cancelling the invoice is not billed
		again, unless ``reissue_cancelled`` is set (for example after cancelling a wrong batch).
		"""
		filters = {"fee_structure": self.name}
		if reissue_cancelled:
			filters["docstatus"] = ("<", 2)
		return set(frappe.get_all(INVOICE, filters=filters, pluck="student", distinct=True))

	@frappe.whitelist()
	def get_publish_preview(self, reissue_cancelled: bool = False) -> dict:
		self.check_permission("read")
		reissue_cancelled = cint(reissue_cancelled)
		students = self.get_matching_students(frappe.session.user)
		invoiced = self.get_invoiced_students(bool(reissue_cancelled))
		pending = [s for s in students if s not in invoiced]
		cancelled_only = 0
		if not reissue_cancelled:
			active = self.get_invoiced_students(True)
			cancelled_only = len([s for s in students if s in invoiced and s not in active])
		return {
			"matching_students": len(students),
			"already_invoiced": len(students) - len(pending),
			"to_invoice": len(pending),
			"cancelled_only": cancelled_only,
			"amount_per_student": money(self.total_amount),
			"total_amount": money(self.total_amount * len(pending)),
			"due_date": self.due_date,
			"is_running": self.is_publish_running(),
		}

	def is_publish_running(self) -> bool:
		return self.publish_status in ("Queued", "In Progress") and is_job_enqueued(publish_job_id(self.name))

	@frappe.whitelist(methods=["POST"])
	def publish_dues(self, reissue_cancelled: bool = False) -> dict:
		"""Queue one invoice per matching student (background job on the long queue)."""
		self.check_permission("write")
		frappe.has_permission(INVOICE, "create", throw=True)
		frappe.has_permission(INVOICE, "submit", throw=True)

		saved = frappe.get_doc(STRUCTURE, self.name)
		if str(saved.modified) != str(self.modified):
			frappe.throw(_("Save the fee structure before publishing dues."))
		if not saved.is_active:
			frappe.throw(_("Activate the fee structure before publishing dues."))
		if saved.is_publish_running():
			frappe.throw(_("Dues for this structure are already being published."))

		saved.db_set(
			{"publish_status": "Queued", "publish_log": _("Queued by {0}").format(frappe.session.user)},
			notify=True,
		)
		frappe.enqueue(
			"hostel360.hostel_fees.doctype.hostel_fee_structure.hostel_fee_structure.run_publish_dues",
			queue=PUBLISH_QUEUE,
			timeout=PUBLISH_TIMEOUT,
			job_id=publish_job_id(self.name),
			deduplicate=True,
			enqueue_after_commit=True,
			structure=self.name,
			user=frappe.session.user,
			reissue_cancelled=cint(reissue_cancelled),
		)
		return {"queued": True}


def run_publish_dues(structure: str, user: str | None = None, reissue_cancelled: int = 0) -> dict:
	"""Background job: create and submit an invoice for every matching student not yet invoiced.

	Safe to re-run; students who already have an invoice from the structure are skipped. One
	student's failure is logged and does not stop the others.
	"""
	from hostel360.hostel_fees.services import create_invoice

	doc = frappe.get_doc(STRUCTURE, structure)
	doc.db_set("publish_status", "In Progress", notify=True)
	_commit()

	created, skipped, failed = 0, 0, []
	try:
		invoiced = doc.get_invoiced_students(bool(cint(reissue_cancelled)))
		items = [
			{"fee_head": row.fee_head, "description": row.description, "amount": row.amount}
			for row in doc.items
		]
		for student in doc.get_matching_students(user):
			if student in invoiced:
				skipped += 1
				continue
			savepoint = "hostel360_publish_dues"
			frappe.db.savepoint(savepoint)
			try:
				create_invoice(
					student,
					items,
					"Hostel Fee",
					doc.due_date,
					reference_doctype=STRUCTURE,
					reference_name=doc.name,
					posting_date=nowdate() if getdate(nowdate()) <= getdate(doc.due_date) else doc.due_date,
					academic_year=doc.academic_year,
					fee_structure=doc.name,
				)
				created += 1
			except Exception:
				frappe.db.rollback(save_point=savepoint)
				failed.append(student)
				frappe.log_error(
					title=f"Publish dues: invoice for {student} failed",
					reference_doctype=STRUCTURE,
					reference_name=doc.name,
				)
			if created and created % COMMIT_EVERY == 0:
				_commit()
	except Exception:
		frappe.db.rollback()
		doc.db_set(
			{
				"publish_status": "Failed",
				"publish_log": _("Publishing stopped after {0} invoices. See the Error Log.").format(created),
			},
			notify=True,
		)
		frappe.log_error(
			title=f"Publish dues failed for {structure}",
			reference_doctype=STRUCTURE,
			reference_name=structure,
		)
		_commit()
		raise

	total_created = cint(frappe.db.count(INVOICE, {"fee_structure": doc.name, "docstatus": ("<", 2)}))
	log = _("{0} invoices created, {1} students already invoiced.").format(created, skipped)
	if failed:
		log += " " + _("Failed for {0}: {1}").format(len(failed), ", ".join(failed[:50]))
	doc.db_set(
		{
			"publish_status": "Completed with Errors" if failed else "Completed",
			"invoices_created": total_created,
			"last_published_on": now_datetime(),
			"last_published_by": user or frappe.session.user,
			"publish_log": log,
		},
		notify=True,
	)
	_commit()

	if user:
		frappe.publish_realtime(
			"hostel360_dues_published",
			{"structure": doc.name, "created": created, "skipped": skipped, "failed": len(failed)},
			user=user,
			after_commit=True,
		)
	return {"created": created, "skipped": skipped, "failed": failed}


def _commit():
	if not frappe.in_test:
		frappe.db.commit()  # nosemgrep
