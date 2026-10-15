"""Scheduled jobs for grievances."""

import frappe
from frappe.query_builder import DocType
from frappe.utils import now_datetime

from hostel360.student_services.doctype.hostel_grievance.hostel_grievance import (
	MAX_ESCALATION_LEVEL,
	OPEN_STATUSES,
)


def overdue_grievances(now=None) -> list[str]:
	"""Open grievances whose action deadline has passed, oldest deadline first.

	The deadline is compared as it is. A filter dict would wrap it in IFNULL and count a
	grievance with no deadline at all as overdue.
	"""
	grievance = DocType("Hostel Grievance")
	return (
		frappe.qb.from_(grievance)
		.select(grievance.name)
		.where(grievance.status.isin(OPEN_STATUSES))
		.where(grievance.sla_due_by.isnotnull())
		.where(grievance.sla_due_by < (now or now_datetime()))
		.where(grievance.escalation_level < MAX_ESCALATION_LEVEL)
		.orderby(grievance.sla_due_by)
		.run(pluck=True)
	)


def escalate_overdue_grievances():
	"""Hourly: escalate open grievances that have passed their action deadline.

	Each escalation moves the deadline forward by another SLA period, so a grievance that
	stays unresolved climbs one level per period instead of alerting every hour.
	"""
	grievances = overdue_grievances()
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
