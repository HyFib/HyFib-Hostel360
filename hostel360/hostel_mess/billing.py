"""Monthly mess billing on the dividing system (design §4.4).

    total_cost      = sum(issue value in period) + sum(submitted Mess Expense) - other_credits
    chargeable_days = enrolled_days(student, period) - approved_reduction_days(student, period)
    daily_rate      = round(total_cost / sum(chargeable_days), 2)
    bill            = chargeable_days * daily_rate + sum(unbilled Mess Extra Charge in period)

Enrolled days come from submitted Room Allotments (not Cancelled) in the hostels the mess
serves, clipped to the period and to the stay (from the allotment date to the day the
student vacated, the planned end date, or the period end). Allotments of one student are
merged day by day, so a transfer between two served hostels is never counted twice, and an
allotment that was transferred ends the day before its successor starts, so the day of a
move is billed once even when the two hostels are served by different messes.

Students are charged for days only while they are enrolled in the mess. ``mess_enrolled``
is a flag on the student; its changes are dated from the student's version history (the
DocType tracks changes), so a student who opts out or joins during the period is charged
for the enrolled days only. Reduction days are the approved reduction days that fall on
charged days.
"""

import importlib
import json
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, format_date, getdate

from hostel360.hostel_mess.stock import get_issue_value
from hostel360.hostel_mess.utils import (
	date_range_days,
	get_mess_hostels,
	round_half_up,
	student_hostel_fields,
)

MESS_CHARGES_FEE_HEAD = "Mess Charges"
MESS_EXTRAS_FEE_HEAD = "Mess Extras"
MAX_PERIOD_DAYS = 92


@dataclass
class StudentBill:
	student: str
	student_name: str = ""
	hostel: str | None = None
	enrolled_days: int = 0
	reduction_days: int = 0
	extras_amount: float = 0.0
	daily_rate: float = 0.0
	mess_charge: float = 0.0
	rounding_adjustment: float = 0.0
	total_amount: float = 0.0
	extra_charges: list = field(default_factory=list)

	@property
	def chargeable_days(self) -> int:
		return max(cint(self.enrolled_days) - cint(self.reduction_days), 0)


@dataclass
class BillRunResult:
	provision_cost: float
	other_expenses: float
	other_credits: float
	total_cost: float
	total_student_days: int = 0
	daily_rate: float = 0.0
	bills: list = field(default_factory=list)
	# Residents whose mess enrolment changed on or after the start of the period.
	enrolment_changes: dict = field(default_factory=dict)

	@property
	def total_extras(self) -> float:
		return flt(sum(b.extras_amount for b in self.bills), 2)

	@property
	def total_billed(self) -> float:
		return flt(sum(b.total_amount for b in self.bills), 2)

	@property
	def rounding_adjustment(self) -> float:
		return flt(sum(b.rounding_adjustment for b in self.bills), 2)

	@property
	def rate_rounding_difference(self) -> float:
		return flt(sum(b.mess_charge for b in self.bills) - self.total_cost, 2)


def divide_cost(
	total_cost: float, bills: list[StudentBill], rounding: str = "Nearest Rupee"
) -> tuple[int, float]:
	"""Apply the dividing system to students whose days and extras are known.

	Fills in each bill's rate, charge, rounding and total; returns
	``(total_student_days, daily_rate)``.
	"""
	total_days = sum(b.chargeable_days for b in bills)
	if total_days <= 0:
		frappe.throw(
			_(
				"There are no chargeable student-days in this period, so the mess cost cannot be divided. "
				"Check that residents have submitted room allotments in the hostels this mess serves, "
				"that they are enrolled in the mess, and that approved reductions do not cover the whole period."
			),
			title=_("Nothing to Divide"),
		)
	daily_rate = round_half_up(flt(total_cost) / total_days, 2)
	for bill in bills:
		bill.daily_rate = daily_rate
		bill.mess_charge = round_half_up(bill.chargeable_days * daily_rate, 2)
		bill.extras_amount = round_half_up(bill.extras_amount, 2)
		unrounded = round_half_up(bill.mess_charge + bill.extras_amount, 2)
		total = round_half_up(unrounded, 0) if rounding == "Nearest Rupee" else unrounded
		bill.total_amount = flt(total, 2)
		bill.rounding_adjustment = round_half_up(total - unrounded, 2)
	return total_days, daily_rate


def get_other_expenses(mess: str, from_date, to_date) -> float:
	value = frappe.db.sql(
		"""
		select coalesce(sum(amount), 0) from `tabMess Expense`
		where mess = %(mess)s and docstatus = 1 and posting_date between %(from_date)s and %(to_date)s
		""",
		{"mess": mess, "from_date": getdate(from_date), "to_date": getdate(to_date)},
	)[0][0]
	return flt(value, 2)


def get_enrolled_days(mess: str, from_date, to_date) -> dict[str, dict]:
	"""Days each resident of the mess's hostels stayed during the period.

	Returns ``{student: {"days": set of date ordinals, "hostel": last hostel}}`` (all
	residents, enrolled in the mess or not).
	"""
	hostels = get_mess_hostels(mess)
	if not hostels:
		return {}
	period_start, period_end = getdate(from_date), getdate(to_date)
	allotments = frappe.db.sql(
		"""
		select name, student, hostel, from_date, to_date, vacated_on
		from `tabRoom Allotment`
		where docstatus = 1 and ifnull(status, '') != 'Cancelled'
			and hostel in %(hostels)s
			and from_date <= %(period_end)s
			and coalesce(vacated_on, to_date, %(period_end)s) >= %(period_start)s
		order by from_date asc, name asc
		""",
		{"hostels": tuple(hostels), "period_start": period_start, "period_end": period_end},
		as_dict=True,
	)
	if not allotments:
		return {}

	# A transfer starts a new allotment that points at the old one. The old one ends the day
	# before its successor begins (Room Allotment records the move day itself as vacated_on,
	# and a legacy transfer may have no end date at all), so the move day belongs to the new
	# hostel only, whichever mess serves it.
	successor_start = dict(
		frappe.db.sql(
			"""
			select previous_allotment, min(from_date)
			from `tabRoom Allotment`
			where docstatus = 1 and ifnull(status, '') != 'Cancelled'
				and previous_allotment in %(names)s
			group by previous_allotment
			""",
			{"names": tuple(a.name for a in allotments)},
		)
	)

	result = {}
	for allotment in allotments:
		start = max(getdate(allotment.from_date), period_start)
		end = getdate(allotment.vacated_on or allotment.to_date or period_end)
		if allotment.name in successor_start:
			end = min(end, getdate(add_days(successor_start[allotment.name], -1)))
		end = min(end, period_end)
		days = date_range_days(start, end)
		if not days:
			continue
		entry = result.setdefault(allotment.student, {"days": set(), "hostel": None, "last_day": 0})
		entry["days"] |= days
		if max(days) >= entry["last_day"]:
			entry["last_day"] = max(days)
			entry["hostel"] = allotment.hostel
	return result


def get_enrolment_changes(students: list[str], from_date) -> dict[str, list[tuple]]:
	"""Dated changes of ``mess_enrolled`` from the students' version history.

	Returns ``{student: [(date, enrolled_before, enrolled_after), ...]}`` in the order they
	happened, for changes made on or after ``from_date``. A change takes effect on the day it
	was made. Changes written without a version (direct database updates) cannot be dated and
	are not listed; for those students the current flag applies to the whole period.
	"""
	if not students:
		return {}
	rows = frappe.db.sql(
		"""
		select docname, creation, data
		from `tabVersion`
		where ref_doctype = 'Hostel Student' and docname in %(students)s
			and creation >= %(from_date)s and data like %(pattern)s
		order by creation asc, name asc
		""",
		{"students": tuple(students), "from_date": getdate(from_date), "pattern": '%"mess_enrolled"%'},
		as_dict=True,
	)
	changes = {}
	for row in rows:
		try:
			data = json.loads(row.data or "{}")
		except ValueError:
			continue
		for change in data.get("changed") or []:
			if len(change) != 3 or change[0] != "mess_enrolled":
				continue
			before, after = cint(change[1]), cint(change[2])
			if before != after:
				changes.setdefault(row.docname, []).append((getdate(row.creation), before, after))
	return changes


def get_enrolled_day_set(days: set[int], enrolled_now, changes: list[tuple] | None) -> set[int]:
	"""The days (ordinals) of ``days`` on which the student was enrolled in the mess."""
	if not changes:
		return set(days) if cint(enrolled_now) else set()
	enrolled = changes[0][1]
	pending = list(changes)
	result = set()
	for day in sorted(days):
		while pending and pending[0][0].toordinal() <= day:
			enrolled = pending.pop(0)[2]
		if enrolled:
			result.add(day)
	return result


def get_reduction_days(students: list[str], from_date, to_date) -> dict[str, set[int]]:
	if not students:
		return {}
	rows = frappe.db.sql(
		"""
		select student, from_date, to_date
		from `tabMess Reduction Application`
		where status = 'Approved' and student in %(students)s
			and from_date <= %(to_date)s and to_date >= %(from_date)s
		""",
		{"students": tuple(students), "from_date": getdate(from_date), "to_date": getdate(to_date)},
		as_dict=True,
	)
	result = {}
	for row in rows:
		result.setdefault(row.student, set()).update(date_range_days(row.from_date, row.to_date))
	return result


def get_unbilled_extras(mess: str, from_date, to_date) -> dict[str, list]:
	rows = frappe.get_all(
		"Mess Extra Charge",
		filters={
			"mess": mess,
			"status": "Unbilled",
			"charge_date": ["between", [getdate(from_date), getdate(to_date)]],
		},
		fields=["name", "student", "student_name", "hostel", "amount"],
		order_by="charge_date asc, name asc",
	)
	result = {}
	for row in rows:
		result.setdefault(row.student, []).append(row)
	return result


def build_bill_run(
	mess: str, from_date, to_date, other_credits=0, rounding: str = "Nearest Rupee"
) -> BillRunResult:
	"""Compute every student's bill for a period without writing anything."""
	from_date, to_date = getdate(from_date), getdate(to_date)
	provision_cost = get_issue_value(mess, from_date, to_date)
	other_expenses = get_other_expenses(mess, from_date, to_date)
	other_credits = flt(other_credits, 2)
	total_cost = flt(provision_cost + other_expenses - other_credits, 2)
	result = BillRunResult(
		provision_cost=provision_cost,
		other_expenses=other_expenses,
		other_credits=other_credits,
		total_cost=total_cost,
	)
	if total_cost < 0:
		frappe.throw(_("Other credits ({0}) are more than the cost of the period.").format(other_credits))
	if total_cost == 0:
		frappe.throw(
			_(
				"No mess cost is recorded for {0} to {1}. Submit the stock issues and expenses of the period before computing bills."
			).format(format_date(from_date), format_date(to_date)),
			title=_("Nothing to Divide"),
		)

	residence = get_enrolled_days(mess, from_date, to_date)
	extras = get_unbilled_extras(mess, from_date, to_date)
	students = sorted(set(residence) | set(extras))
	details = (
		{
			row.name: row
			for row in frappe.get_all(
				"Hostel Student",
				filters={"name": ["in", students]},
				fields=["name", "full_name", "mess_enrolled", *student_hostel_fields()],
			)
		}
		if students
		else {}
	)
	result.enrolment_changes = get_enrolment_changes(list(residence), from_date)
	enrolled = {}
	for student, entry in residence.items():
		days = get_enrolled_day_set(
			entry["days"],
			(details.get(student) or {}).get("mess_enrolled"),
			result.enrolment_changes.get(student),
		)
		if days:
			enrolled[student] = {**entry, "days": days}
	reductions = get_reduction_days(list(enrolled), from_date, to_date)

	bills = []
	for student in students:
		info = details.get(student) or frappe._dict()
		stay = enrolled.get(student)
		student_extras = extras.get(student, [])
		if not stay and not student_extras:
			continue
		days = stay["days"] if stay else set()
		bill = StudentBill(
			student=student,
			student_name=info.get("full_name") or (student_extras[0].student_name if student_extras else ""),
			# The hostel the student stayed in during the period; for extras alone, the hostel
			# recorded on the charge, then where the student lives or last lived.
			hostel=(residence.get(student) or {}).get("hostel")
			or (student_extras[0].hostel if student_extras else None)
			or info.get("current_hostel")
			or info.get("last_hostel"),
			enrolled_days=len(days),
			reduction_days=len(reductions.get(student, set()) & days),
			extras_amount=flt(sum(flt(e.amount) for e in student_extras), 2),
			extra_charges=[e.name for e in student_extras],
		)
		bills.append(bill)

	result.total_student_days, result.daily_rate = divide_cost(total_cost, bills, rounding)
	result.bills = bills
	return result


def count_residents(mess: str, from_date, to_date) -> int:
	"""Rough number of bills a run will produce; decides whether to compute in the background."""
	hostels = get_mess_hostels(mess)
	if not hostels:
		return 0
	return cint(
		frappe.db.sql(
			"""
			select count(distinct student) from `tabRoom Allotment`
			where docstatus = 1 and ifnull(status, '') != 'Cancelled' and hostel in %(hostels)s
				and from_date <= %(to_date)s and coalesce(vacated_on, to_date, %(to_date)s) >= %(from_date)s
			""",
			{"hostels": tuple(hostels), "from_date": getdate(from_date), "to_date": getdate(to_date)},
		)[0][0]
	)


def validate_period(from_date, to_date) -> None:
	if getdate(from_date) > getdate(to_date):
		frappe.throw(_("From Date cannot be after To Date."))
	if date_diff(to_date, from_date) + 1 > MAX_PERIOD_DAYS:
		frappe.throw(_("A bill run can cover at most {0} days.").format(MAX_PERIOD_DAYS))


def get_cost_breakdown(mess: str, from_date, to_date) -> dict:
	"""Item-wise provisions and expense-type totals for statements and print formats."""
	values = {"mess": mess, "from_date": getdate(from_date), "to_date": getdate(to_date)}
	items = frappe.db.sql(
		"""
		select l.item, i.uom, sum(-l.actual_qty) as qty, sum(-l.stock_value_difference) as amount
		from `tabMess Stock Ledger Entry` l
		left join `tabMess Item` i on i.name = l.item
		where l.mess = %(mess)s and l.is_cancelled = 0 and l.voucher_type = 'Mess Stock Entry'
			and l.entry_type = 'Issue' and l.posting_date between %(from_date)s and %(to_date)s
		group by l.item, i.uom
		order by amount desc
		""",
		values,
		as_dict=True,
	)
	expenses = frappe.db.sql(
		"""
		select expense_type, sum(amount) as amount
		from `tabMess Expense`
		where mess = %(mess)s and docstatus = 1 and posting_date between %(from_date)s and %(to_date)s
		group by expense_type
		order by amount desc
		""",
		values,
		as_dict=True,
	)
	return {
		"items": [
			{"item": r.item, "uom": r.uom, "qty": flt(r.qty, 3), "amount": flt(r.amount, 2)} for r in items
		],
		"expenses": [{"expense_type": r.expense_type, "amount": flt(r.amount, 2)} for r in expenses],
	}


# ------------------------------------------------------------------ fee invoices


def get_fees_service(name: str):
	"""Hostel Fees service function, resolved at call time."""
	try:
		module = importlib.import_module("hostel360.hostel_fees.services")
	except ImportError:
		module = None
	fn = getattr(module, name, None) if module else None
	if not fn:
		frappe.throw(_("The Hostel Fees module is not available, so mess bills cannot be invoiced."))
	return fn


def ensure_fee_heads() -> None:
	missing = [
		head
		for head in (MESS_CHARGES_FEE_HEAD, MESS_EXTRAS_FEE_HEAD)
		if not frappe.db.exists("Hostel Fee Head", head)
	]
	if missing:
		frappe.throw(
			_("Create the fee head(s) {0} before publishing mess bills.").format(", ".join(missing)),
			title=_("Fee Heads Missing"),
		)


def get_bill_invoices(bill_names: list[str]) -> dict[str, list[frappe._dict]]:
	"""Every invoice raised for each of the given mess bills, cancelled ones included.

	The bill's ``fee_invoice`` names the invoice the bill run created. The office may later
	cancel that invoice and amend it; the amendment keeps the reference to the bill, so
	invoices are found by reference as well as through the link.
	"""
	if not bill_names or not frappe.db.table_exists("Hostel Fee Invoice"):
		return {}
	linked = frappe.get_all(
		"Mess Bill",
		filters={"name": ["in", bill_names], "fee_invoice": ["is", "set"]},
		fields=["name", "fee_invoice"],
	)
	names = {row.fee_invoice for row in linked}
	names.update(
		frappe.get_all(
			"Hostel Fee Invoice",
			filters={"reference_doctype": "Mess Bill", "reference_name": ["in", bill_names]},
			pluck="name",
		)
	)
	if not names:
		return {}
	invoices = {
		row.name: row
		for row in frappe.get_all(
			"Hostel Fee Invoice",
			filters={"name": ["in", list(names)]},
			fields=[
				"name",
				"reference_doctype",
				"reference_name",
				"docstatus",
				"status",
				"paid_amount",
				"outstanding_amount",
				"due_date",
				"creation",
			],
		)
	}
	result = {}
	for row in linked:
		if row.fee_invoice in invoices:
			result.setdefault(row.name, {})[row.fee_invoice] = invoices[row.fee_invoice]
	for invoice in invoices.values():
		if invoice.reference_doctype == "Mess Bill" and invoice.reference_name in bill_names:
			result.setdefault(invoice.reference_name, {})[invoice.name] = invoice
	return {bill: sorted(rows.values(), key=lambda r: (r.creation, r.name)) for bill, rows in result.items()}


def get_current_invoice(invoices: list[frappe._dict] | None) -> frappe._dict | None:
	"""The invoice that stands for a bill now: the latest submitted one, else the latest."""
	if not invoices:
		return None
	submitted = [row for row in invoices if row.docstatus == 1]
	return (submitted or invoices)[-1]


def get_invoice_payment_state(invoices: list[str]) -> list[dict]:
	"""Invoices among the given ones that have received any payment."""
	if not invoices:
		return []
	rows = frappe.get_all(
		"Hostel Fee Invoice",
		filters={"name": ["in", invoices]},
		fields=["name", "status", "paid_amount", "docstatus"],
	)
	return [
		row
		for row in rows
		if row.docstatus != 2 and (flt(row.paid_amount) > 0 or row.status in ("Paid", "Partly Paid"))
	]


def sync_amended_invoice(doc, method=None) -> None:
	"""Point a mess bill at the amendment that replaced its invoice (Hostel Fee Invoice on_submit)."""
	if (
		doc.get("reference_doctype") != "Mess Bill"
		or not doc.get("reference_name")
		or not doc.get("amended_from")
	):
		return
	if frappe.db.exists("Mess Bill", doc.reference_name):
		frappe.db.set_value("Mess Bill", doc.reference_name, "fee_invoice", doc.name, update_modified=False)
