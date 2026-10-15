# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import inspect
import time

import frappe
from frappe import _
from frappe.model.document import DOCUMENT_LOCK_EXPIRY, Document
from frappe.utils import (
	add_days,
	cint,
	file_lock,
	flt,
	format_date,
	get_last_day,
	getdate,
	now_datetime,
	nowdate,
	strip_html,
)
from frappe.utils.scheduler import is_scheduler_inactive

from hostel360.hostel_mess import billing
from hostel360.hostel_mess.permissions import assert_mess_access
from hostel360.hostel_mess.utils import (
	BooksChangedError,
	get_mess_settings,
	in_migration,
	lock_mess_books,
	send_notification,
	start_books_transaction,
	student_recipients,
)
from hostel360.utils.jinja import format_inr

RUN_MODULE = "hostel360.hostel_mess.doctype.mess_bill_run.mess_bill_run"

# Above this many residents (or bills) the work runs in a background worker: computing the
# bills, and publishing or cancelling them (one fee invoice per bill).
BACKGROUND_THRESHOLD = 250
# Attempts a background publish or cancel makes when the books change under it.
JOB_ATTEMPTS = 3
# Progress is reported for runs at least this large, every PROGRESS_STEP bills.
PROGRESS_MIN_BILLS = 50
PROGRESS_STEP = 25

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

ACTION_LABELS = {"submit": "publish", "cancel": "cancel"}


class MessBillRun(Document):
	def validate(self):
		billing.validate_period(self.from_date, self.to_date)
		if not in_migration() and getdate(self.to_date) > getdate(nowdate()):
			frappe.throw(
				_("Bills can be computed only for a period that has ended (To Date on or before today).")
			)
		if flt(self.other_credits) < 0:
			frappe.throw(_("Other credits cannot be negative."))
		assert_mess_access(self.mess, doctype=self.doctype)
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
		self.set_onload("runs_in_background", self.should_run_in_background())
		self.set_onload("action_queued", self.is_action_queued())
		if self.docstatus == 1:
			summary = {}
			for invoice in self.get_current_invoices().values():
				row = summary.setdefault(
					invoice.status, frappe._dict(status=invoice.status, count=0, outstanding=0.0)
				)
				row.count += 1
				row.outstanding = flt(row.outstanding + flt(invoice.outstanding_amount), 2)
			self.set_onload("invoice_summary", list(summary.values()))

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
		self.report_enrolment_changes(result)
		return result

	def report_enrolment_changes(self, result):
		"""Point out residents whose mess enrolment changed during (or after) the period."""
		changed = sorted(result.enrolment_changes)
		if not changed:
			return
		names = ", ".join(frappe.bold(s) for s in changed[:10])
		more = _(" and {0} more").format(len(changed) - 10) if len(changed) > 10 else ""
		frappe.msgprint(
			_(
				"The mess enrolment of {0} resident(s) changed on or after {1}: {2}{3}. They are charged only for the days they were enrolled; please check their bills before publishing."
			).format(len(changed), format_date(self.from_date), names, more),
			title=_("Enrolment Changes"),
			indicator="orange",
		)

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

	# ------------------------------------------------------------------ publish and cancel

	@frappe.whitelist()
	def submit(self):
		"""Publish the bills: in a background job for large runs, otherwise right away."""
		return self.run_publishing_action("submit")

	@frappe.whitelist()
	def cancel(self):
		"""Withdraw the bills and their invoices: in a background job for large runs."""
		return self.run_publishing_action("cancel")

	def run_publishing_action(self, action: str):
		if self.should_run_in_background():
			return self.queue_publishing_action(action)
		# Read the books afresh under the exclusive books lock (see utils.start_books_transaction).
		start_books_transaction(self.mess)
		return getattr(self, f"_{action}")()

	def is_action_queued(self) -> bool:
		"""Whether a publish or cancel is waiting for (or running in) a background worker.

		Reads the document lock directly: Document.is_locked may also add the viewer as a
		follower, which must not happen while a form is merely being opened.
		"""
		if not self.creation:
			return False
		signature = self.get_signature()
		return file_lock.lock_exists(signature) and file_lock.lock_age(signature) <= DOCUMENT_LOCK_EXPIRY

	def should_run_in_background(self) -> bool:
		if self.flags.in_background_job or not self.creation:
			return False
		return cint(self.bills_count) > BACKGROUND_THRESHOLD and not is_scheduler_inactive(verbose=False)

	def queue_publishing_action(self, action: str) -> dict:
		"""Check what can be checked now, then hand the work to a worker on the long queue."""
		stored = frappe.get_doc(self.doctype, self.name)
		if action == "submit":
			if stored.docstatus != 0:
				frappe.throw(_("This bill run is already {0}.").format(_(stored.status).lower()))
			stored.check_permission("submit")
			stored.check_ready_to_publish()
		else:
			if stored.docstatus != 1:
				frappe.throw(_("Only a published bill run can be cancelled."))
			stored.check_permission("cancel")
			stored.check_invoices_unpaid()
		stored.check_if_locked()
		stored.lock()
		frappe.enqueue(
			f"{RUN_MODULE}.run_publishing_action_in_background",
			queue="long",
			timeout=3600,
			job_id=f"mess-bill-run::{self.name}",
			deduplicate=True,
			enqueue_after_commit=True,
			name=self.name,
			mess=self.mess,
			action=action,
			user=frappe.session.user,
		)
		# The caller (for example the desk's save handler) sends this document back to the
		# browser; it must show the run as it is until the worker has finished.
		self.docstatus = stored.docstatus
		self.status = stored.status
		frappe.msgprint(
			_(
				"{0} {1} bills in the background. You can leave this page; it refreshes when the work is done."
			).format(_("Publishing") if action == "submit" else _("Cancelling"), cint(stored.bills_count)),
			indicator="blue",
			alert=True,
		)
		return {"queued": True}

	def check_ready_to_publish(self):
		if self.status != "Computed" or not cint(self.bills_count):
			frappe.throw(_("Compute the bills before publishing."))

	def before_submit(self):
		# Exclusive books lock: entries being posted for this mess finish first, later ones
		# wait for this publish and then find the period closed.
		lock_mess_books(self.mess, exclusive=True)
		self.validate_overlap()
		self.check_ready_to_publish()
		self.verify_computation()
		self.due_date = add_days(nowdate(), cint(get_mess_settings().bill_due_days))
		self.status = "Published"

	def on_submit(self):
		self.publish_bills()
		self.update_billed_upto()

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
		for done, bill in enumerate(bills, start=1):
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
			self.report_progress(done, len(bills), _("Publishing mess bills"))

		for bill in published:
			if flt(bill.total_amount) > 0:
				self.notify_student(bill)

	def report_progress(self, done: int, total: int, title: str) -> None:
		if total < PROGRESS_MIN_BILLS or (done % PROGRESS_STEP and done != total):
			return
		frappe.publish_progress(
			done * 100 / total,
			title=title,
			doctype=self.doctype,
			docname=self.name,
			description=_("{0} of {1} bills").format(done, total),
		)

	def update_billed_upto(self):
		"""Record on the mess the last day its published bill runs cover (under the books lock)."""
		upto = frappe.db.sql(
			"select max(to_date) from `tabMess Bill Run` where mess = %(mess)s and docstatus = 1",
			{"mess": self.mess},
		)[0][0]
		frappe.db.set_value("Hostel Mess", self.mess, "billed_upto", upto, update_modified=False)

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

	def check_invoices_unpaid(self):
		paid = billing.get_invoice_payment_state(self.get_invoices())
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

	def before_cancel(self):
		lock_mess_books(self.mess, exclusive=True)
		self.check_invoices_unpaid()
		self.status = "Cancelled"

	def on_cancel(self):
		self.ignore_linked_doctypes = ("Mess Bill", "Hostel Fee Invoice", "Mess Extra Charge")
		invoices = self.get_invoices()
		if invoices:
			cancel_invoice = billing.get_fees_service("cancel_invoice")
			for done, invoice in enumerate(invoices, start=1):
				cancel_invoice(invoice)
				self.report_progress(done, len(invoices), _("Cancelling mess bill invoices"))

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
		self.update_billed_upto()

	def on_trash(self):
		if self.docstatus == 0:
			frappe.db.delete("Mess Bill", {"mess_bill_run": self.name, "status": "Draft"})

	def get_invoices(self) -> list[str]:
		"""Invoices of this run that are not cancelled, including amendments made by the office."""
		bills = frappe.get_all("Mess Bill", filters={"mess_bill_run": self.name}, pluck="name")
		return sorted(
			invoice.name
			for rows in billing.get_bill_invoices(bills).values()
			for invoice in rows
			if invoice.docstatus != 2
		)

	def get_current_invoices(self, bill_names: list[str] | None = None) -> dict[str, frappe._dict]:
		"""``{bill: invoice}``: the invoice that stands for each bill of the run now."""
		if bill_names is None:
			bill_names = frappe.get_all("Mess Bill", filters={"mess_bill_run": self.name}, pluck="name")
		return {
			bill: current
			for bill, rows in billing.get_bill_invoices(bill_names).items()
			if (current := billing.get_current_invoice(rows))
		}

	# ------------------------------------------------------------------ print helpers

	def get_cost_breakdown(self) -> dict:
		return billing.get_cost_breakdown(self.mess, self.from_date, self.to_date)

	def get_bills(self) -> list[dict]:
		"""Bills the viewer may see. get_list applies role and hostel User Permissions, so
		hostel-scoped staff (deputy wardens, office staff) see only their hostels' rows."""
		bills = frappe.get_list(
			"Mess Bill",
			filters={"mess_bill_run": self.name},
			fields=BILL_FIELDS,
			order_by="hostel asc, student asc",
			limit=0,
		)
		current = self.get_current_invoices([b.name for b in bills])
		for bill in bills:
			if bill.name in current:
				bill.fee_invoice = current[bill.name].name
		return bills


@frappe.whitelist(methods=["POST"])
def compute(name: str) -> dict:
	"""Compute the bills of a draft run, in the background for large hostels."""
	run = frappe.get_doc("Mess Bill Run", name)
	run.check_permission("write")
	if run.docstatus != 0:
		frappe.throw(_("Bills can be computed only while the bill run is a draft."))
	assert_mess_access(run.mess, doctype=run.doctype)

	if billing.count_residents(run.mess, run.from_date, run.to_date) > BACKGROUND_THRESHOLD:
		frappe.enqueue(
			f"{RUN_MODULE}.compute_in_background",
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
		result = run.compute_bills()
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
		{
			"name": name,
			"bills_count": run.bills_count,
			"daily_rate": run.daily_rate,
			"enrolment_changes": len(result.enrolment_changes),
		},
		user=user,
		after_commit=True,
	)


@frappe.whitelist(methods=["POST"])
def start_action(name: str, action: str) -> dict:
	"""Publish (submit) or cancel a bill run from the form, queueing large runs."""
	if action not in ACTION_LABELS:
		frappe.throw(_("Unknown action {0}.").format(action))
	run = frappe.get_doc("Mess Bill Run", name)
	result = run.submit() if action == "submit" else run.cancel()
	if isinstance(result, dict):
		return result
	return {"queued": False, "status": run.status}


def run_publishing_action_in_background(name: str, mess: str, action: str, user: str | None = None) -> None:
	"""Worker: publish or cancel a bill run, reporting progress and the outcome in real time.

	Each attempt starts a new transaction whose first statement is the exclusive books lock,
	so the bills are verified against everything committed before the lock. When the books
	change under an attempt anyway (a deadlock, or snapshot isolation refusing a row), the
	work is rolled back and tried again.
	"""
	if action not in ACTION_LABELS:
		return
	for attempt in range(1, JOB_ATTEMPTS + 1):
		try:
			# Nothing of this job is pending at this point; end whatever the worker read while
			# starting up so the lock below opens the transaction and every read follows it.
			frappe.db.commit()
			lock_mess_books(mess, exclusive=True)
			run = frappe.get_doc("Mess Bill Run", name)
			run.unlock()
			run.flags.in_background_job = True
			getattr(run, f"_{action}")()
			frappe.db.commit()
		except BooksChangedError, frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt < JOB_ATTEMPTS:
				time.sleep(attempt)
				continue
			report_background_failure(
				name,
				action,
				user,
				_("The mess books kept changing while the bills were being processed. Please try again."),
			)
			return
		except (frappe.ValidationError, frappe.PermissionError) as e:
			frappe.db.rollback()
			report_background_failure(name, action, user, strip_html(str(e)))
			return
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Mess bill run {name}: {ACTION_LABELS[action]} failed",
				reference_doctype="Mess Bill Run",
				reference_name=name,
			)
			report_background_failure(
				name,
				action,
				user,
				_("The work failed unexpectedly. The error has been logged for support."),
			)
			return
		frappe.publish_realtime(
			"mess_bill_run_action_done",
			{"name": name, "action": action, "status": run.status},
			user=user,
		)
		run.notify_update()
		return


def report_background_failure(name: str, action: str, user: str | None, error: str) -> None:
	"""Tell the user now, and leave a note on the run for anyone who opens it later.

	Called after the failed work has been rolled back; commits the note (and any error log).
	"""
	if frappe.db.exists("Mess Bill Run", name):
		run = frappe.get_doc("Mess Bill Run", name)
		run.unlock()
		label = _("Publishing the bills failed") if action == "submit" else _("Cancelling the bills failed")
		run.add_comment("Comment", f"{label}: {frappe.utils.escape_html(error)}")
		frappe.db.commit()
		run.notify_update()
	frappe.publish_realtime(
		"mess_bill_run_action_failed",
		{"name": name, "action": action, "error": error or _("The work could not be completed.")},
		user=user,
	)
