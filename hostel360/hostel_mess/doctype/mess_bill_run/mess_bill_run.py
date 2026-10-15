# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import inspect

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import (
	add_days,
	cint,
	flt,
	format_date,
	get_last_day,
	getdate,
	now_datetime,
	nowdate,
	strip_html,
)

from hostel360.hostel_mess import billing
from hostel360.hostel_mess.utils import (
	get_mess_settings,
	in_migration,
	send_notification,
	student_recipients,
)
from hostel360.utils.jinja import format_inr

# Above this many residents the bills are computed by a background worker.
BACKGROUND_THRESHOLD = 250

BILL_FIELDS = [
	"name",
	"student",
	"student_name",
	"hostel",
	"enrolled_days",
	"reduction_days",
	"chargeable_days",
	"daily_rate",
	"mess_charge",
	"extras_amount",
	"rounding_adjustment",
	"total_amount",
	"fee_invoice",
	"status",
]


class MessBillRun(Document):
	def validate(self):
		billing.validate_period(self.from_date, self.to_date)
		if not in_migration() and getdate(self.to_date) > getdate(nowdate()):
			frappe.throw(
				_("Bills can be computed only for a period that has ended (To Date on or before today).")
			)
		if flt(self.other_credits) < 0:
			frappe.throw(_("Other credits cannot be negative."))
		self.validate_overlap()

		if self.docstatus == 0:
			if self.status not in ("Draft", "Computed"):
				self.status = "Draft"
			if (
				not self.is_new()
				and self.status == "Computed"
				and not self.flags.from_compute
				and any(self.has_value_changed(f) for f in ("to_date", "other_credits"))
			):
				self.status = "Draft"
				frappe.msgprint(
					_("The period or credits changed. Click Compute Bills again before publishing."),
					indicator="orange",
					alert=True,
				)

	def validate_overlap(self):
		clash = frappe.db.get_value(
			"Mess Bill Run",
			{
				"mess": self.mess,
				"docstatus": ["<", 2],
				"name": ["!=", self.name or ""],
				"from_date": ["<=", self.to_date],
				"to_date": [">=", self.from_date],
			},
			["name", "from_date", "to_date"],
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("Bill run {0} already covers {1} to {2} for this mess.").format(
					frappe.bold(clash.name), format_date(clash.from_date), format_date(clash.to_date)
				),
				title=_("Overlapping Bill Run"),
			)

	def onload(self):
		if self.docstatus == 1 and frappe.db.table_exists("Hostel Fee Invoice"):
			summary = frappe.db.sql(
				"""
				select i.status, count(*) as count, sum(i.outstanding_amount) as outstanding
				from `tabMess Bill` b
				inner join `tabHostel Fee Invoice` i on i.name = b.fee_invoice
				where b.mess_bill_run = %(run)s
				group by i.status
				""",
				{"run": self.name},
				as_dict=True,
			)
			self.set_onload("invoice_summary", summary)

	# ------------------------------------------------------------------ compute

	def compute_bills(self):
		"""Create (or recreate) the draft bills of this run. Safe to run repeatedly."""
		frappe.db.sql(
			"select name from `tabMess Bill Run` where name = %(name)s for update", {"name": self.name}
		)
		self.reload()
		if self.docstatus != 0:
			frappe.throw(_("Bills can be computed only while the bill run is a draft."))

		settings = get_mess_settings()
		result = billing.build_bill_run(
			self.mess, self.from_date, self.to_date, self.other_credits, settings.bill_rounding
		)

		frappe.db.delete("Mess Bill", {"mess_bill_run": self.name, "status": "Draft"})
		for bill in result.bills:
			doc = frappe.get_doc(
				{
					"doctype": "Mess Bill",
					"mess_bill_run": self.name,
					"mess": self.mess,
					"student": bill.student,
					"student_name": bill.student_name,
					"hostel": bill.hostel,
					"from_date": self.from_date,
					"to_date": self.to_date,
					"enrolled_days": bill.enrolled_days,
					"reduction_days": bill.reduction_days,
					"chargeable_days": bill.chargeable_days,
					"daily_rate": bill.daily_rate,
					"mess_charge": bill.mess_charge,
					"extras_amount": bill.extras_amount,
					"rounding_adjustment": bill.rounding_adjustment,
					"total_amount": bill.total_amount,
					"status": "Draft",
				}
			)
			doc.flags.from_bill_run = True
			doc.flags.ignore_links = True
			doc.insert(ignore_permissions=True)

		self.update(
			{
				"provision_cost": result.provision_cost,
				"other_expenses": result.other_expenses,
				"total_cost": result.total_cost,
				"total_student_days": result.total_student_days,
				"daily_rate": result.daily_rate,
				"bills_count": len(result.bills),
				"total_extras": result.total_extras,
				"total_billed": result.total_billed,
				"rate_rounding_difference": result.rate_rounding_difference,
				"rounding_adjustment": result.rounding_adjustment,
				"status": "Computed",
				"computed_on": now_datetime(),
			}
		)
		self.flags.from_compute = True
		try:
			self.save(ignore_permissions=True)
		finally:
			self.flags.from_compute = False
		return result

	def verify_computation(self):
		"""Refuse to publish bills that no longer match the books."""
		settings = get_mess_settings()
		result = billing.build_bill_run(
			self.mess, self.from_date, self.to_date, self.other_credits, settings.bill_rounding
		)
		stored = {
			row.student: row
			for row in frappe.get_all(
				"Mess Bill",
				filters={"mess_bill_run": self.name, "status": "Draft"},
				fields=["student", "chargeable_days", "extras_amount", "total_amount"],
			)
		}
		changes = []
		if abs(result.total_cost - flt(self.total_cost)) >= 0.005:
			changes.append(
				_("total mess cost is now {0} (computed {1})").format(
					format_inr(result.total_cost), format_inr(self.total_cost)
				)
			)
		fresh = {bill.student: bill for bill in result.bills}
		if set(fresh) != set(stored):
			changes.append(_("the list of students has changed"))
		else:
			differing = [
				student
				for student, bill in fresh.items()
				if bill.chargeable_days != cint(stored[student].chargeable_days)
				or abs(bill.extras_amount - flt(stored[student].extras_amount)) >= 0.005
				or abs(bill.total_amount - flt(stored[student].total_amount)) >= 0.005
			]
			if differing:
				changes.append(
					_("{0} bill(s) differ, for example {1}").format(len(differing), ", ".join(differing[:3]))
				)
		if changes:
			frappe.throw(
				_(
					"The figures changed after the bills were computed: {0}. Click Compute Bills again."
				).format("; ".join(changes)),
				title=_("Recompute Required"),
			)

	# ------------------------------------------------------------------ publish

	def before_submit(self):
		if self.status != "Computed" or not cint(self.bills_count):
			frappe.throw(_("Compute the bills before publishing."))
		self.verify_computation()
		self.due_date = add_days(nowdate(), cint(get_mess_settings().bill_due_days))
		self.status = "Published"

	def on_submit(self):
		self.publish_bills()

	def publish_bills(self):
		bills = frappe.get_all(
			"Mess Bill",
			filters={"mess_bill_run": self.name, "status": "Draft"},
			fields=BILL_FIELDS,
			order_by="student asc",
		)
		to_invoice = [b for b in bills if not b.fee_invoice and flt(b.total_amount) > 0]
		create_invoice = None
		if to_invoice:
			create_invoice = billing.get_fees_service("create_invoice")
			billing.ensure_fee_heads()

		# The invoice should carry the hostel the student was billed in, which differs from the
		# current hostel for residents who moved or left; pass it when the fees service accepts it.
		pass_hostel = bool(create_invoice) and "hostel" in inspect.signature(create_invoice).parameters

		published = []
		for bill in bills:
			invoice = bill.fee_invoice
			if not invoice and flt(bill.total_amount) > 0:
				extra = {"hostel": bill.hostel} if pass_hostel and bill.hostel else {}
				invoice = create_invoice(
					student=bill.student,
					items=self.get_invoice_items(bill),
					invoice_type="Mess Bill",
					due_date=self.due_date,
					reference_doctype="Mess Bill",
					reference_name=bill.name,
					posting_date=nowdate(),
					remarks=_("Mess bill {0} for {1}").format(bill.name, self.get_period_label()),
					**extra,
				)
			frappe.db.set_value(
				"Mess Bill", bill.name, {"status": "Published", "fee_invoice": invoice}, update_modified=False
			)
			if flt(bill.extras_amount) > 0:
				self.mark_extras_billed(bill)
			bill.fee_invoice = invoice
			published.append(bill)

		for bill in published:
			if flt(bill.total_amount) > 0:
				self.notify_student(bill)

	def get_invoice_items(self, bill) -> list[dict]:
		"""Invoice lines: mess charges and extras. Bill rounding rides on the larger line."""
		mess_charge = flt(bill.mess_charge, 2)
		extras = flt(bill.extras_amount, 2)
		rounding = flt(bill.rounding_adjustment, 2)
		charge_line = flt(mess_charge + (rounding if mess_charge > 0 else 0), 2)
		extras_line = flt(extras + (rounding if mess_charge <= 0 else 0), 2)
		period = self.get_period_label()
		items = []
		if charge_line > 0:
			description = _("Mess charges {0}: {1} day(s) at {2} per day").format(
				period, cint(bill.chargeable_days), format_inr(bill.daily_rate)
			)
			if rounding and mess_charge > 0:
				description += " " + _("(rounding {0})").format(format_inr(rounding))
			items.append(
				{"fee_head": billing.MESS_CHARGES_FEE_HEAD, "description": description, "amount": charge_line}
			)
		if extras_line > 0:
			items.append(
				{
					"fee_head": billing.MESS_EXTRAS_FEE_HEAD,
					"description": _("Mess extras {0}").format(period),
					"amount": extras_line,
				}
			)
		return items

	def mark_extras_billed(self, bill):
		charge = frappe.qb.DocType("Mess Extra Charge")
		(
			frappe.qb.update(charge)
			.set(charge.status, "Billed")
			.set(charge.mess_bill, bill.name)
			.where(charge.mess == self.mess)
			.where(charge.student == bill.student)
			.where(charge.status == "Unbilled")
			.where(charge.charge_date[getdate(self.from_date) : getdate(self.to_date)])
		).run()

	def notify_student(self, bill):
		send_notification(
			"mess_bill_published",
			student_recipients(bill.student, include_student=True, include_guardians=True),
			{
				"bill": bill.name,
				"student": bill.student,
				"student_name": bill.student_name,
				"mess": self.mess,
				"period": self.get_period_label(),
				"from_date": str(getdate(self.from_date)),
				"to_date": str(getdate(self.to_date)),
				"chargeable_days": cint(bill.chargeable_days),
				"daily_rate": flt(bill.daily_rate, 2),
				"mess_charge": flt(bill.mess_charge, 2),
				"extras_amount": flt(bill.extras_amount, 2),
				"amount": flt(bill.total_amount, 2),
				"due_date": str(getdate(self.due_date)),
				"invoice": bill.fee_invoice,
			},
			reference_doctype="Mess Bill",
			reference_name=bill.name,
		)

	def get_period_label(self) -> str:
		"""'September 2026' for a calendar month, otherwise the date range."""
		start, end = getdate(self.from_date), getdate(self.to_date)
		if start.day == 1 and end == getdate(get_last_day(start)):
			return start.strftime("%B %Y")
		return _("{0} to {1}").format(format_date(start), format_date(end))

	# ------------------------------------------------------------------ cancel

	def before_cancel(self):
		invoices = self.get_invoices()
		paid = billing.get_invoice_payment_state(invoices)
		if paid:
			names = ", ".join(row.name for row in paid[:10])
			more = _(" and {0} more").format(len(paid) - 10) if len(paid) > 10 else ""
			frappe.throw(
				_(
					"Payments have been received against {0} invoice(s) of this bill run ({1}{2}). "
					"A bill run can be cancelled only while none of its invoices is paid."
				).format(len(paid), names, more),
				title=_("Invoices Already Paid"),
			)
		self.status = "Cancelled"

	def on_cancel(self):
		self.ignore_linked_doctypes = ("Mess Bill", "Hostel Fee Invoice", "Mess Extra Charge")
		invoices = self.get_invoices()
		if invoices:
			cancel_invoice = billing.get_fees_service("cancel_invoice")
			open_invoices = frappe.get_all(
				"Hostel Fee Invoice", filters={"name": ["in", invoices], "docstatus": ["!=", 2]}, pluck="name"
			)
			for invoice in open_invoices:
				cancel_invoice(invoice)

		bill_names = frappe.get_all("Mess Bill", filters={"mess_bill_run": self.name}, pluck="name")
		if bill_names:
			bill = frappe.qb.DocType("Mess Bill")
			frappe.qb.update(bill).set(bill.status, "Cancelled").where(bill.name.isin(bill_names)).run()
			charge = frappe.qb.DocType("Mess Extra Charge")
			(
				frappe.qb.update(charge)
				.set(charge.status, "Unbilled")
				.set(charge.mess_bill, None)
				.where(charge.mess_bill.isin(bill_names))
			).run()

	def on_trash(self):
		if self.docstatus == 0:
			frappe.db.delete("Mess Bill", {"mess_bill_run": self.name, "status": "Draft"})

	def get_invoices(self) -> list[str]:
		return frappe.get_all(
			"Mess Bill",
			filters={"mess_bill_run": self.name, "fee_invoice": ["is", "set"]},
			pluck="fee_invoice",
		)

	# ------------------------------------------------------------------ print helpers

	def get_cost_breakdown(self) -> dict:
		return billing.get_cost_breakdown(self.mess, self.from_date, self.to_date)

	def get_bills(self) -> list[dict]:
		return frappe.get_all(
			"Mess Bill",
			filters={"mess_bill_run": self.name},
			fields=BILL_FIELDS,
			order_by="hostel asc, student asc",
		)


@frappe.whitelist(methods=["POST"])
def compute(name: str) -> dict:
	"""Compute the bills of a draft run, in the background for large hostels."""
	run = frappe.get_doc("Mess Bill Run", name)
	run.check_permission("write")
	if run.docstatus != 0:
		frappe.throw(_("Bills can be computed only while the bill run is a draft."))

	if billing.count_residents(run.mess, run.from_date, run.to_date) > BACKGROUND_THRESHOLD:
		frappe.enqueue(
			"hostel360.hostel_mess.doctype.mess_bill_run.mess_bill_run.compute_in_background",
			queue="long",
			timeout=1800,
			job_id=f"mess-bill-run-compute::{name}",
			deduplicate=True,
			enqueue_after_commit=True,
			name=name,
			user=frappe.session.user,
		)
		return {"queued": True}

	run.compute_bills()
	return {"queued": False, "bills_count": run.bills_count, "daily_rate": run.daily_rate}


def compute_in_background(name: str, user: str | None = None) -> None:
	"""Worker entry point for large runs; reports the outcome to the user in real time."""
	savepoint = "mess_bill_run_compute"
	frappe.db.savepoint(savepoint)
	try:
		run = frappe.get_doc("Mess Bill Run", name)
		run.compute_bills()
	except frappe.ValidationError as e:
		frappe.db.rollback(save_point=savepoint)
		frappe.publish_realtime(
			"mess_bill_run_compute_failed",
			{"name": name, "error": strip_html(str(e)) or _("Could not compute the bills.")},
			user=user,
		)
		return
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(
			title=f"Mess bill run {name}: compute failed",
			reference_doctype="Mess Bill Run",
			reference_name=name,
		)
		frappe.publish_realtime(
			"mess_bill_run_compute_failed",
			{
				"name": name,
				"error": _("Computing the bills failed unexpectedly. The error has been logged for support."),
			},
			user=user,
		)
		raise
	frappe.publish_realtime(
		"mess_bill_run_computed",
		{"name": name, "bills_count": run.bills_count, "daily_rate": run.daily_rate},
		user=user,
		after_commit=True,
	)
