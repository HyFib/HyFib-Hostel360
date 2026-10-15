"""Scheduled jobs for grievances."""

import frappe
from frappe.utils import now_datetime

from hostel360.student_services.doctype.hostel_grievance.hostel_grievance import (
	MAX_ESCALATION_LEVEL,
	OPEN_STATUSES,
)


def escalate_overdue_grievances():
	"""Hourly: escalate open grievances that have passed their action deadline.

	Each escalation moves the deadline forward by another SLA period, so a grievance that
	stays unresolved climbs one level per period instead of alerting every hour.
	"""
	grievances = frappe.get_all(
		"Hostel Grievance",
		filters={
			"status": ["in", OPEN_STATUSES],
			"sla_due_by": ["<", now_datetime()],
			"escalation_level": ["<", MAX_ESCALATION_LEVEL],
		},
		pluck="name",
		order_by="sla_due_by asc",
	)
	for name in grievances:
		frappe.db.savepoint("grievance_escalation")
		try:
			doc = frappe.get_doc("Hostel Grievance", name, for_update=True)
			doc.escalate()
		except Exception:
			frappe.db.rollback(save_point="grievance_escalation")
			frappe.log_error(
				title="Hostel360: could not escalate grievance",
				reference_doctype="Hostel Grievance",
				reference_name=name,
			)
	return len(grievances)
