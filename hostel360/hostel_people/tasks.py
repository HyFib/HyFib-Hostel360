"""Scheduled jobs of the People module."""

import frappe
from frappe.utils import getdate, nowdate


def close_ended_allotments() -> None:
	"""Vacate active allotments whose planned end date has passed (daily).

	Each allotment is committed on its own so that one bad record (for example a bed
	locked by an office user at that moment) does not hold back the rest.
	"""
	from hostel360.hostel_people.doctype.room_allotment.room_allotment import PERIOD_ENDED_REASON

	due = frappe.get_all(
		"Room Allotment",
		filters={"docstatus": 1, "status": "Active", "to_date": ["<", getdate(nowdate())]},
		fields=["name", "to_date"],
		order_by="to_date asc",
	)
	closed = failed = 0
	for row in due:
		try:
			doc = frappe.get_doc("Room Allotment", row.name)
			doc.vacate_allotment(row.to_date, PERIOD_ENDED_REASON)
			frappe.db.commit()  # nosemgrep
			closed += 1
		except Exception:
			frappe.db.rollback()
			failed += 1
			frappe.log_error(
				title="Hostel360: could not close ended allotment",
				message=frappe.get_traceback(),
				reference_doctype="Room Allotment",
				reference_name=row.name,
			)

	if due:
		frappe.logger("hostel360").info(f"close_ended_allotments: {closed} closed, {failed} failed")
	report_to_operations(failed, len(due))


ALERT_KEY = "people:close-ended-allotments"


def report_to_operations(failed: int, due: int) -> None:
	"""Beds stay blocked while an ended allotment cannot be closed, so HyFib support is told."""
	try:
		from hostel360.hostel_operations.alerts import raise_alert, resolve_alert
	except ImportError:
		return

	if failed:
		raise_alert(
			ALERT_KEY,
			"Room allotments could not be closed automatically",
			"Warning",
			"Jobs",
			description=(
				f"{failed} of {due} allotments past their end date could not be vacated by the daily job. "
				"Their beds remain occupied until they are vacated. See the Error Log for details."
			),
			details={"failed": failed, "due": due},
		)
	else:
		resolve_alert(ALERT_KEY, notes="The daily job closed every ended allotment.")
