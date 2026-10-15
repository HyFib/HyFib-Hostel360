"""Fee reminders (daily at 08:00 IST).

An invoice is due for a reminder when it falls due within the configured window (3 days by
default) or is overdue. Reminders go out per student, not per invoice: one message lists all of
the student's dues, and a family hears from us at most once per interval (3 days by default),
however many invoices they have with different due dates. Each invoice is reminded at most
``max_reminders_per_invoice`` times.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, formatdate, getdate, nowdate

from hostel360.hostel_fees import notifications
from hostel360.hostel_fees.constants import INVOICE, PAYABLE_STATUSES
from hostel360.hostel_fees.utils import DISPLAY_DATE_FORMAT, get_payment_settings, money
from hostel360.utils.jinja import format_inr

COMMIT_EVERY = 200
DEFAULT_DAYS_BEFORE_DUE = 3
DEFAULT_INTERVAL_DAYS = 3
# Invoice numbers named in one message; the rest are summarised as "and N more".
MAX_INVOICES_NAMED = 3


def send_fee_reminders() -> int:
	"""Queue today's reminders. Returns the number of students reminded."""
	settings = get_payment_settings()
	if not cint(settings.get("send_fee_reminders")):
		return 0

	days_before = cint(settings.get("reminder_days_before_due")) or DEFAULT_DAYS_BEFORE_DUE
	interval = cint(settings.get("reminder_interval_days")) or DEFAULT_INTERVAL_DAYS
	max_reminders = cint(settings.get("max_reminders_per_invoice"))
	today = getdate(nowdate())
	recent = add_days(today, -interval)

	invoices = frappe.get_all(
		INVOICE,
		filters=[
			["docstatus", "=", 1],
			["outstanding_amount", ">", 0],
			["status", "in", PAYABLE_STATUSES],
		],
		fields=["name", "student", "due_date", "last_reminder_on", "reminder_count"],
		order_by="due_date asc, name asc",
	)

	by_student = {}
	for invoice in invoices:
		by_student.setdefault(invoice.student, []).append(invoice)

	sent = 0
	for student, open_invoices in by_student.items():
		# Any reminder within the interval, for any of the student's dues, counts for the family.
		if any(row.last_reminder_on and getdate(row.last_reminder_on) > recent for row in open_invoices):
			continue
		due = [
			row
			for row in open_invoices
			if row.due_date
			and getdate(row.due_date) <= add_days(today, days_before)
			and (not max_reminders or cint(row.reminder_count) < max_reminders)
		]
		if not due:
			continue
		try:
			send_student_reminder(student, [row.name for row in due], today)
			sent += 1
		except Exception:
			frappe.log_error(
				title=f"Fee reminder for {student} failed",
				reference_doctype="Hostel Student",
				reference_name=student,
			)
		if sent and sent % COMMIT_EVERY == 0:
			_commit()
	_commit()
	return sent


def send_student_reminder(student: str, invoice_names: list[str], today=None) -> None:
	"""Queue one reminder covering ``invoice_names`` and record it on each invoice."""
	today = getdate(today or nowdate())
	invoices = sorted(
		(frappe.get_doc(INVOICE, name) for name in invoice_names),
		key=lambda doc: (getdate(doc.due_date), doc.name),
	)
	if not invoices:
		return

	earliest = invoices[0]
	context = earliest.get_notification_context()
	outstanding = money(sum(money(doc.outstanding_amount) for doc in invoices))
	days_overdue = date_diff(today, earliest.due_date) if earliest.due_date else 0
	names = [doc.name for doc in invoices]
	context.update(
		{
			# The templates name one invoice; with several, the others are summarised.
			"invoice": _invoice_label(names),
			"invoice_count": len(invoices),
			"invoices": [
				{
					"invoice": doc.name,
					"invoice_type": doc.invoice_type,
					"outstanding": money(doc.outstanding_amount),
					"outstanding_formatted": format_inr(doc.outstanding_amount),
					"due_date": str(doc.due_date),
					"due_date_formatted": formatdate(doc.due_date, DISPLAY_DATE_FORMAT),
				}
				for doc in invoices
			],
			"outstanding": outstanding,
			"outstanding_amount": outstanding,
			"outstanding_formatted": format_inr(outstanding),
			"is_overdue": days_overdue > 0,
			"days_overdue": max(days_overdue, 0),
			"days_to_due": max(-days_overdue, 0),
		}
	)
	notifications.notify_student(
		notifications.EVENT_FEE_REMINDER,
		student,
		context,
		reference_doctype=INVOICE,
		reference_name=earliest.name,
	)
	for doc in invoices:
		frappe.db.set_value(
			INVOICE,
			doc.name,
			{"last_reminder_on": today, "reminder_count": cint(doc.reminder_count) + 1},
			update_modified=False,
		)


def send_invoice_reminder(invoice, today=None) -> None:
	"""Queue a reminder for one invoice now (desk action) and record when it was sent."""
	send_student_reminder(invoice.student, [invoice.name], today)


def _invoice_label(names: list[str]) -> str:
	if len(names) <= MAX_INVOICES_NAMED:
		return ", ".join(names)
	return _("{0} and {1} more").format(", ".join(names[:MAX_INVOICES_NAMED]), len(names) - MAX_INVOICES_NAMED)


def _commit():
	if not frappe.in_test:
		frappe.db.commit()  # nosemgrep
