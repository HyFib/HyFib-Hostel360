"""Scheduled jobs for home visit leave."""

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from hostel360.student_services.utils import get_setting

OVERDUE_CANDIDATE_STATUSES = ("Approved", "Checked Out")


def mark_overdue_returns():
	"""Hourly: flag leaves whose return time plus the grace period has passed."""
	grace_hours = max(cint(get_setting("leave_return_grace_hours")), 0)
	cutoff = add_to_date(now_datetime(), hours=-grace_hours)
	leaves = frappe.get_all(
		"Home Visit Leave",
		filters={"status": ["in", OVERDUE_CANDIDATE_STATUSES], "to_datetime": ["<", cutoff]},
		pluck="name",
		order_by="to_datetime asc",
	)
	for name in leaves:
		frappe.db.savepoint("leave_overdue")
		try:
			doc = frappe.get_doc("Home Visit Leave", name, for_update=True)
			if doc.status not in OVERDUE_CANDIDATE_STATUSES:
				continue
			doc.mark_overdue()
		except Exception:
			frappe.db.rollback(save_point="leave_overdue")
			frappe.log_error(
				title="Hostel360: could not mark leave overdue",
				reference_doctype="Home Visit Leave",
				reference_name=name,
			)
	return len(leaves)
