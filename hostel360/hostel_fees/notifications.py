"""Student and parent notifications raised by the fee module.

Messages are queued only after the surrounding transaction commits, so a payment that is
rolled back can never produce a "payment received" SMS.
"""

import frappe

from hostel360.hostel_fees.utils import notifications_suppressed

EVENT_FEE_DUE_PUBLISHED = "fee_due_published"
EVENT_FEE_REMINDER = "fee_reminder"
EVENT_PAYMENT_RECEIVED = "payment_received"


def notify_student(
	event: str,
	student: str,
	context: dict,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	include_guardians: bool = True,
) -> None:
	if notifications_suppressed() or not student:
		return
	# "event" is a parameter of frappe.enqueue itself, hence the different name.
	_queue(
		notification_event=event,
		student=student,
		context=context,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		include_guardians=include_guardians,
	)


def _queue(**kwargs) -> None:
	frappe.enqueue(
		"hostel360.hostel_fees.notifications.dispatch",
		queue="short",
		enqueue_after_commit=True,
		**kwargs,
	)


def dispatch(
	notification_event: str,
	student: str,
	context: dict,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	include_guardians: bool = True,
) -> None:
	"""Background job: resolve recipients now (they may have changed) and hand over to messaging."""
	event = notification_event
	try:
		from hostel360.hostel_communication.dispatch import notify, recipients_for_student
	except ImportError:
		frappe.logger("hostel360").warning(f"Messaging is unavailable; {event} for {student} was not sent")
		return

	try:
		recipients = recipients_for_student(
			student, include_student=True, include_guardians=include_guardians
		)
		if recipients:
			notify(
				event,
				recipients,
				context,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
	except Exception:
		frappe.log_error(
			title=f"Hostel360: {event} notification failed",
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
