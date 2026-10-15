"""Mess services used by the student/parent portal, the approvals page and other modules.

These functions hold the business rules for portal callers. They do not rely on DocType
permissions for students and parents (portal users have none); ownership is checked
explicitly with ``assert_can_access_student`` and staff actions with role and document
permissions.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime, nowdate

from hostel360.hostel_mess.utils import (
	MEAL_ORDER,
	WEEKDAYS,
	assert_period_open,
	get_mess_for_hostel,
	get_reduction_decider_roles,
	get_week_start,
	send_notification,
	student_recipients,
)
from hostel360.utils.context import assert_can_access_student, is_staff

REDUCTION = "Mess Reduction Application"


# ------------------------------------------------------------------ mess and menu


def get_mess_for_student(student: str) -> str | None:
	"""The active mess serving the student's current hostel."""
	hostel = frappe.db.get_value("Hostel Student", student, "current_hostel")
	return get_mess_for_hostel(hostel)


def get_week_menu(mess: str, week_start: str | None = None, published_only: bool = True) -> dict:
	"""Menu of a mess for the week containing ``week_start`` (default: this week).

	Returns ``{mess, week_start, week_end, status, published, notes, days}`` where ``days`` is
	one entry per day (Monday first) with its meals in serving order. ``days`` is empty when
	no menu exists (or, with ``published_only``, none is published yet).
	"""
	start = get_week_start(week_start or nowdate())
	end = frappe.utils.add_days(start, 6)
	result = {
		"mess": mess,
		"week_start": str(start),
		"week_end": str(getdate(end)),
		"status": None,
		"published": False,
		"notes": None,
		"days": [],
	}
	if not mess:
		return result

	filters = {"mess": mess, "week_start_date": start}
	if published_only:
		filters["status"] = "Published"
	menu = frappe.db.get_value("Mess Menu", filters, ["name", "status", "notes"], as_dict=True)
	if not menu:
		return result

	rows = frappe.get_all(
		"Mess Menu Item",
		filters={"parent": menu.name, "parenttype": "Mess Menu"},
		fields=["menu_date", "meal", "dishes", "is_special"],
		order_by="menu_date asc, idx asc",
	)
	by_date = {}
	for row in rows:
		by_date.setdefault(getdate(row.menu_date), []).append(row)

	today = getdate(nowdate())
	days = []
	for offset in range(7):
		date = getdate(frappe.utils.add_days(start, offset))
		meals = sorted(
			by_date.get(date, []),
			key=lambda r: MEAL_ORDER.index(r.meal) if r.meal in MEAL_ORDER else len(MEAL_ORDER),
		)
		days.append(
			{
				"date": str(date),
				"day": WEEKDAYS[date.weekday()],
				"is_today": date == today,
				"meals": [
					{"meal": m.meal, "dishes": m.dishes, "is_special": bool(m.is_special)} for m in meals
				],
			}
		)
	result.update(status=menu.status, published=menu.status == "Published", notes=menu.notes, days=days)
	return result


def get_today_menu(student: str) -> dict | None:
	"""Today's published meals at the student's mess, or None."""
	mess = get_mess_for_student(student)
	if not mess:
		return None
	week = get_week_menu(mess, nowdate())
	today = str(getdate(nowdate()))
	for day in week["days"]:
		if day["date"] == today and day["meals"]:
			return {"mess": mess, "date": day["date"], "day": day["day"], "meals": day["meals"]}
	return None


# ------------------------------------------------------------------ bills


def list_mess_bills(student: str) -> list[dict]:
	"""Published mess bills of a student, newest first, with the invoice status."""
	bills = frappe.get_all(
		"Mess Bill",
		filters={"student": student, "status": "Published"},
		fields=[
			"name",
			"mess",
			"mess_bill_run",
			"from_date",
			"to_date",
			"enrolled_days",
			"reduction_days",
			"chargeable_days",
			"daily_rate",
			"mess_charge",
			"extras_amount",
			"rounding_adjustment",
			"total_amount",
			"fee_invoice",
		],
		order_by="from_date desc",
	)
	invoices = {}
	invoice_names = [b.fee_invoice for b in bills if b.fee_invoice]
	if invoice_names and frappe.db.table_exists("Hostel Fee Invoice"):
		invoices = {
			row.name: row
			for row in frappe.get_all(
				"Hostel Fee Invoice",
				filters={"name": ["in", invoice_names]},
				fields=["name", "status", "outstanding_amount", "due_date"],
			)
		}
	result = []
	for bill in bills:
		invoice = invoices.get(bill.fee_invoice) or {}
		result.append(
			{
				"name": bill.name,
				"mess": bill.mess,
				"from_date": str(bill.from_date),
				"to_date": str(bill.to_date),
				"enrolled_days": bill.enrolled_days,
				"reduction_days": bill.reduction_days,
				"chargeable_days": bill.chargeable_days,
				"daily_rate": flt(bill.daily_rate, 2),
				"mess_charge": flt(bill.mess_charge, 2),
				"extras_amount": flt(bill.extras_amount, 2),
				"rounding_adjustment": flt(bill.rounding_adjustment, 2),
				"total_amount": flt(bill.total_amount, 2),
				"fee_invoice": bill.fee_invoice,
				"invoice_status": invoice.get("status") if bill.fee_invoice else None,
				"outstanding_amount": flt(invoice.get("outstanding_amount"), 2) if invoice else 0.0,
				"due_date": str(invoice.get("due_date")) if invoice.get("due_date") else None,
			}
		)
	return result


# ------------------------------------------------------------------ reductions


def list_mess_reductions(student: str) -> list[dict]:
	rows = frappe.get_all(
		REDUCTION,
		filters={"student": student},
		fields=[
			"name",
			"from_date",
			"to_date",
			"number_of_days",
			"reason",
			"description",
			"status",
			"approval_remarks",
			"decided_on",
			"home_visit_leave",
			"creation",
		],
		order_by="from_date desc, creation desc",
	)
	today = getdate(nowdate())
	return [
		{
			"name": row.name,
			"from_date": str(row.from_date),
			"to_date": str(row.to_date),
			"number_of_days": row.number_of_days,
			"reason": row.reason,
			"description": row.description,
			"status": row.status,
			"approval_remarks": row.approval_remarks,
			"decided_on": str(row.decided_on) if row.decided_on else None,
			"home_visit_leave": row.home_visit_leave,
			"applied_on": str(row.creation),
			"can_cancel": row.status == "Pending"
			or (row.status == "Approved" and getdate(row.from_date) > today),
		}
		for row in rows
	]


def apply_mess_reduction(
	student: str,
	from_date,
	to_date,
	reason: str,
	description: str | None = None,
	home_visit_leave: str | None = None,
	user: str | None = None,
) -> str:
	"""Create a pending mess reduction application and alert the approvers."""
	user = user or frappe.session.user
	assert_can_access_student(student, user)

	doc = frappe.new_doc(REDUCTION)
	doc.update(
		{
			"student": student,
			"hostel": frappe.db.get_value("Hostel Student", student, "current_hostel"),
			"from_date": getdate(from_date),
			"to_date": getdate(to_date),
			"reason": reason,
			"description": (description or "").strip() or None,
			"home_visit_leave": home_visit_leave,
			"applied_by": user,
		}
	)
	if is_staff(user) and not frappe.has_permission(REDUCTION, "create", doc=doc, user=user):
		frappe.throw(_("You are not permitted to apply for mess reduction."), frappe.PermissionError)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def cancel_mess_reduction(name: str, user: str) -> None:
	"""Withdraw an application.

	Students and parents may cancel while it is pending, or once approved until the day
	the absence starts; staff with write access may cancel any open application whose
	period has not been billed.
	"""
	doc = frappe.get_doc(REDUCTION, name)
	if doc.status not in ("Pending", "Approved"):
		frappe.throw(_("This application is already {0}.").format(_(doc.status).lower()))

	if is_staff(user):
		if not frappe.has_permission(REDUCTION, "write", doc=doc, user=user):
			frappe.throw(_("You are not permitted to cancel this application."), frappe.PermissionError)
	else:
		assert_can_access_student(doc.student, user)
		if doc.status == "Approved" and getdate(doc.from_date) <= getdate(nowdate()):
			frappe.throw(
				_("This reduction has already started. Please contact the hostel office to change it.")
			)

	if doc.status == "Approved":
		assert_period_open(doc.mess, doc.from_date, _("Mess reductions"))

	doc.status = "Cancelled"
	doc.flags.status_change = True
	doc.flags.ignore_permissions = True
	doc.save()


def can_decide_mess_reduction(doc, user: str) -> bool:
	if user == "Administrator":
		return True
	if not set(frappe.get_roles(user)) & get_reduction_decider_roles():
		return False
	return bool(frappe.has_permission(REDUCTION, "write", doc=doc, user=user))


def decide_mess_reduction(name: str, approve: bool, remarks: str | None, user: str) -> None:
	"""Approve or reject a pending application and tell the student and guardians."""
	doc = frappe.get_doc(REDUCTION, name)
	if not can_decide_mess_reduction(doc, user):
		frappe.throw(
			_("You are not permitted to decide mess reduction applications."), frappe.PermissionError
		)
	if doc.status != "Pending":
		frappe.throw(_("This application is already {0}.").format(_(doc.status).lower()))

	approve = _as_bool(approve)
	remarks = (remarks or "").strip() or None
	if approve:
		assert_period_open(doc.mess, doc.from_date, _("Mess reductions"))
	elif not remarks:
		frappe.throw(_("Please give a reason for rejecting the application."))

	doc.status = "Approved" if approve else "Rejected"
	doc.approver = user
	doc.decided_on = now_datetime()
	doc.approval_remarks = remarks
	doc.flags.status_change = True
	doc.flags.ignore_permissions = True
	doc.save()

	context = doc.notification_context()
	context["approved"] = approve
	send_notification(
		"mess_reduction_decided",
		student_recipients(doc.student, include_student=True, include_guardians=True),
		context,
		reference_doctype=REDUCTION,
		reference_name=doc.name,
	)


def _as_bool(value) -> bool:
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "approve", "approved")
	return bool(value)
