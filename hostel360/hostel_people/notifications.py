"""Messages to students and parents raised by the People module.

Delivery (templates, channels, logging) belongs to Hostel Communication. A failed or
unavailable dispatcher must never undo an allotment or a validation that already
happened, so errors are logged and swallowed here.
"""

import frappe


def notify_student(
	event: str,
	student: str,
	context: dict,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	include_guardians: bool = True,
) -> None:
	if frappe.flags.hostel360_migration:
		return

	try:
		from hostel360.hostel_communication.dispatch import notify, recipients_for_student
	except ImportError:
		frappe.logger("hostel360").info(f"Notification dispatcher unavailable; {event} not sent")
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
			message=frappe.get_traceback(),
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
