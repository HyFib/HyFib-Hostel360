"""Fee reminders (daily at 08:00 IST).

An invoice is reminded when it falls due within the configured window (3 days by default) or is
overdue, and at most once per interval (3 days by default) so families are not messaged daily.
"""

import frappe
from frappe.utils import add_days, cint, date_diff, getdate, nowdate

from hostel360.hostel_fees import notifications
from hostel360.hostel_fees.constants import INVOICE, PAYABLE_STATUSES
from hostel360.hostel_fees.utils import get_payment_settings

COMMIT_EVERY = 200
DEFAULT_DAYS_BEFORE_DUE = 3
DEFAULT_INTERVAL_DAYS = 3


def send_fee_reminders() -> int:
	settings = get_payment_settings()
	if not cint(settings.get("send_fee_reminders")):
		return 0

	days_before = cint(settings.get("reminder_days_before_due")) or DEFAULT_DAYS_BEFORE_DUE
	interval = cint(settings.get("reminder_interval_days")) or DEFAULT_INTERVAL_DAYS
	max_reminders = cint(settings.get("max_reminders_per_invoice"))
	today = getdate(nowdate())

	filters = [
		["docstatus", "=", 1],
		["outstanding_amount", ">", 0],
		["status", "in", PAYABLE_STATUSES],
		["due_date", "<=", add_days(today, days_before)],
	]
	if max_reminders:
		filters.append(["reminder_count", "<", max_reminders])

	invoices = frappe.get_all(
		INVOICE,
		filters=filters,
		or_filters=[
			["last_reminder_on", "is", "not set"],
			["last_reminder_on", "<=", add_days(today, -interval)],
		],
		pluck="name",
		order_by="due_date asc",
	)

	sent = 0
	for name in invoices:
		try:
			send_invoice_reminder(frappe.get_doc(INVOICE, name), today)
			sent += 1
		except Exception:
			frappe.log_error(
				title=f"Fee reminder for {name} failed", reference_doctype=INVOICE, reference_name=name
			)
		if sent and sent % COMMIT_EVERY == 0:
			_commit()
	_commit()
	return sent


def send_invoice_reminder(invoice, today=None) -> None:
	"""Queue a reminder for one invoice and record when it was sent."""
	today = getdate(today or nowdate())
	context = invoice.get_notification_context()
	days_overdue = date_diff(today, invoice.due_date) if invoice.due_date else 0
	context.update(
		{
			"is_overdue": days_overdue > 0,
			"days_overdue": max(days_overdue, 0),
			"days_to_due": max(-days_overdue, 0),
		}
	)
	notifications.notify_student(
		notifications.EVENT_FEE_REMINDER,
		invoice.student,
		context,
		reference_doctype=invoice.doctype,
		reference_name=invoice.name,
	)
	frappe.db.set_value(
		INVOICE,
		invoice.name,
		{"last_reminder_on": today, "reminder_count": cint(invoice.reminder_count) + 1},
		update_modified=False,
	)


def _commit():
	if not frappe.in_test:
		frappe.db.commit()  # nosemgrep
