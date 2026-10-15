"""Messaging for Student Services.

Everything goes through ``hostel360.hostel_communication.dispatch``. A failure to queue a
message must never undo a warden's decision or a student's request, so errors are logged
and swallowed here instead of propagating into the business transaction.
"""

import frappe
from frappe.utils import cstr

from hostel360.student_services.utils import get_portal_base_url, in_migration


def _dispatch():
	from hostel360.hostel_communication import dispatch

	return dispatch


def send(event: str, recipients: list[dict], context: dict, doc=None) -> None:
	recipients = dedupe(recipients)
	if not recipients or in_migration():
		return
	try:
		notify = _dispatch().notify
	except (ImportError, AttributeError):
		frappe.logger("hostel360").warning(f"Messaging is not available; {event} not sent")
		return
	try:
		notify(
			event,
			recipients,
			context,
			reference_doctype=doc.doctype if doc else None,
			reference_name=doc.name if doc else None,
		)
	except Exception:
		frappe.log_error(title=f"Hostel360: could not queue {event}", reference_doctype=doc and doc.doctype)


def dedupe(recipients: list[dict] | None) -> list[dict]:
	seen, result = set(), []
	for recipient in recipients or []:
		if not recipient:
			continue
		key = recipient.get("user") or recipient.get("mobile") or recipient.get("email")
		if not key or key in seen:
			continue
		seen.add(key)
		result.append(recipient)
	return result


def for_student(student: str, include_student: bool = True, include_guardians: bool = False) -> list[dict]:
	try:
		return _dispatch().recipients_for_student(
			student, include_student=include_student, include_guardians=include_guardians
		)
	except (ImportError, AttributeError):
		recipients = []
		if include_student:
			row = frappe.db.get_value(
				"Hostel Student", student, ["user", "mobile_number", "email", "full_name"], as_dict=True
			)
			if row:
				recipients.append(
					{"user": row.user, "mobile": row.mobile_number, "email": row.email, "name": row.full_name}
				)
		if include_guardians:
			from hostel360.utils.context import get_guardians

			recipients += for_guardians(get_guardians(student))
		return recipients


def for_guardians(guardians: list[str]) -> list[dict]:
	"""Recipients for specific guardians (for example only those allowed to approve leave)."""
	if not guardians:
		return []
	# A guardian whose link to the student was rejected is not told about the student.
	rows = frappe.get_all(
		"Hostel Guardian",
		filters={"name": ["in", list(guardians)], "validation_status": ["!=", "Rejected"]},
		fields=["user", "mobile_number", "email", "guardian_name"],
		ignore_permissions=True,
	)
	return [
		{"user": r.user, "mobile": r.mobile_number, "email": r.email, "name": r.guardian_name} for r in rows
	]


def for_users(users: list[str]) -> list[dict]:
	users = [u for u in dict.fromkeys(users or []) if u]
	if not users:
		return []
	try:
		return _dispatch().recipients_for_users(users)
	except (ImportError, AttributeError):
		return [{"user": u} for u in users]


def portal_link(path: str) -> str:
	return f"{get_portal_base_url()}/hostel/{path.lstrip('/')}"


def desk_link(doctype: str, name: str) -> str:
	return frappe.utils.get_url_to_form(doctype, name)


# Dates and times are passed in machine format; the message templates format them
# (fdate / fdatetime) consistently for every channel.


def leave_context(doc) -> dict:
	return {
		"leave": doc.name,
		"student": doc.student,
		"student_name": doc.student_name,
		"hostel": doc.hostel,
		"from_datetime": cstr(doc.from_datetime),
		"to_datetime": cstr(doc.to_datetime),
		"destination": doc.destination,
		"reason": doc.reason,
		"status": doc.status,
		"parent_remarks": doc.parent_remarks or "",
		"warden_remarks": doc.warden_remarks or "",
		"checked_out_on": cstr(doc.checked_out_on),
		"returned_on": cstr(doc.returned_on),
		"portal_link": portal_link("leave"),
		"desk_link": desk_link(doc.doctype, doc.name),
	}


def grievance_context(doc, message: str | None = None) -> dict:
	return {
		"grievance": doc.name,
		"student": doc.student,
		# Confidential complaints never carry the student's name in messages to staff.
		"student_name": "" if doc.is_confidential else doc.student_name,
		"hostel": doc.hostel,
		"subject": doc.subject,
		"category": doc.category,
		"priority": doc.priority,
		"status": doc.status,
		"escalation_level": doc.escalation_level or 0,
		"sla_due_by": cstr(doc.sla_due_by),
		"message": message or "",
		"resolution": doc.resolution or "",
		"portal_link": portal_link(f"grievances/{doc.name}"),
		"desk_link": desk_link(doc.doctype, doc.name),
	}


def bonafide_context(doc) -> dict:
	return {
		"request": doc.name,
		"student": doc.student,
		"student_name": doc.student_name,
		"hostel": doc.hostel,
		"purpose": doc.purpose,
		"purpose_details": doc.purpose_details or "",
		"status": doc.status,
		"certificate_number": doc.certificate_number or "",
		"issue_date": cstr(doc.issue_date),
		"valid_till": cstr(doc.valid_till),
		"rejection_reason": doc.rejection_reason or "",
		"portal_link": portal_link("certificates"),
		"desk_link": desk_link(doc.doctype, doc.name),
	}
