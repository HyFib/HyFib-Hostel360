"""Student Services business functions used by the portal API and the desk.

The portal API resolves the caller and checks that they may act for the student before
calling these functions. Functions that take a ``user`` check again, so a mistake in a
caller cannot let one student act on another's records.
"""

import frappe
from frappe import _
from frappe.utils import cint

from hostel360.student_services.utils import is_staff_user, rating_to_stars, stars_to_rating
from hostel360.utils.context import assert_can_access_student, get_guardian_for_user, get_portal_identity

LEAVE = "Home Visit Leave"
GRIEVANCE = "Hostel Grievance"
FEEDBACK = "Hostel Feedback"
BONAFIDE = "Bonafide Certificate Request"
BONAFIDE_PRINT_FORMAT = "Bonafide Certificate"
MAX_GRIEVANCE_ATTACHMENTS = 5
LIST_LIMIT = 100

LEAVE_INPUT_FIELDS = (
	"from_datetime",
	"to_datetime",
	"destination",
	"reason",
	"contact_during_leave",
	"apply_mess_reduction",
)


# ---------------------------------------------------------------------- leave


def list_leaves(student: str) -> list[dict]:
	from hostel360.student_services.doctype.home_visit_leave.home_visit_leave import CANCELLABLE_STATUSES

	rows = frappe.get_all(
		LEAVE,
		filters={"student": student},
		fields=[
			"name",
			"from_datetime",
			"to_datetime",
			"number_of_days",
			"destination",
			"reason",
			"contact_during_leave",
			"applied_by",
			"status",
			"parent_approval_status",
			"parent_remarks",
			"warden_approval_status",
			"warden_remarks",
			"apply_mess_reduction",
			"mess_reduction_days",
			"mess_reduction",
			"checked_out_on",
			"returned_on",
			"creation",
		],
		order_by="from_datetime desc",
		limit=LIST_LIMIT,
	)
	for row in rows:
		row["can_cancel"] = row.status in CANCELLABLE_STATUSES
	return rows


def apply_leave(student: str, data: dict, applied_by: str, user: str) -> str:
	"""Create a leave request. ``applied_by`` is "Student", "Parent" or "Warden"."""
	if applied_by not in ("Student", "Parent", "Warden"):
		frappe.throw(_("Invalid applicant."))
	assert_can_access_student(student, user)
	values = {key: (data or {}).get(key) for key in LEAVE_INPUT_FIELDS}
	values["apply_mess_reduction"] = cint(values.get("apply_mess_reduction"))
	for key in ("destination", "reason", "contact_during_leave"):
		if isinstance(values.get(key), str):
			values[key] = values[key].strip()

	doc = frappe.new_doc(LEAVE)
	doc.update(values)
	doc.student = student
	doc.applied_by = applied_by
	doc.applied_by_user = user

	if applied_by == "Parent":
		guardian = get_guardian_for_user(user)
		if not guardian:
			frappe.throw(
				_("Only a registered parent or guardian can apply on a student's behalf."),
				frappe.PermissionError,
			)
		doc.parent_guardian = guardian
	elif applied_by == "Warden" and not is_staff_user(user):
		frappe.throw(
			_("Only hostel staff can apply for leave on a student's behalf."), frappe.PermissionError
		)
	elif applied_by == "Student":
		identity = get_portal_identity(user)
		if identity.kind != "student" or identity.student != student:
			frappe.throw(_("Students can only apply for their own leave."), frappe.PermissionError)

	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def parent_decision(leave: str, guardian: str, approve: bool, remarks: str | None) -> None:
	doc = frappe.get_doc(LEAVE, leave, for_update=True)
	doc.apply_parent_decision(guardian, bool(approve), remarks)


def warden_decision(leave: str, approve: bool, remarks: str | None, user: str) -> None:
	doc = frappe.get_doc(LEAVE, leave, for_update=True)
	doc.apply_warden_decision(bool(approve), remarks, user)


def cancel_leave(leave: str, user: str) -> None:
	doc = frappe.get_doc(LEAVE, leave, for_update=True)
	if not is_staff_user(user):
		assert_can_access_student(doc.student, user)
	elif not doc.has_permission("write", user=user):
		frappe.throw(_("You are not permitted to cancel this leave."), frappe.PermissionError)
	doc.cancel_by(user)


def list_pending_parent_approvals(guardian: str) -> list[dict]:
	students = frappe.get_all(
		"Hostel Student Guardian",
		filters={"guardian": guardian, "parenttype": "Hostel Student", "can_approve_leave": 1},
		pluck="parent",
	)
	if not students:
		return []
	return frappe.get_all(
		LEAVE,
		filters={"student": ["in", list(set(students))], "status": "Pending Parent Approval"},
		fields=[
			"name",
			"student",
			"student_name",
			"from_datetime",
			"to_datetime",
			"number_of_days",
			"destination",
			"reason",
			"contact_during_leave",
			"apply_mess_reduction",
			"creation",
		],
		order_by="from_datetime asc",
		limit=LIST_LIMIT,
	)


# ---------------------------------------------------------------------- grievances


def list_grievances(student: str) -> list[dict]:
	rows = frappe.get_all(
		GRIEVANCE,
		filters={"student": student},
		fields=[
			"name",
			"subject",
			"category",
			"priority",
			"status",
			"is_confidential",
			"creation",
			"modified",
			"resolved_on",
			"student_rating",
		],
		order_by="creation desc",
		limit=LIST_LIMIT,
	)
	for row in rows:
		row["student_rating"] = rating_to_stars(row.student_rating)
	return rows


def get_grievance(name: str) -> dict:
	"""Grievance with its public timeline. Internal staff notes are never included."""
	doc = frappe.get_doc(GRIEVANCE, name)
	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": GRIEVANCE, "attached_to_name": name},
		fields=["name", "file_name", "file_url", "file_size"],
		order_by="creation asc",
	)
	return {
		"name": doc.name,
		"student": doc.student,
		"subject": doc.subject,
		"category": doc.category,
		"description": doc.description,
		"priority": doc.priority,
		"is_confidential": doc.is_confidential,
		"status": doc.status,
		"creation": doc.creation,
		"sla_due_by": doc.sla_due_by,
		"assigned": bool(doc.assigned_to),
		"resolution": doc.resolution,
		"resolved_on": doc.resolved_on,
		"student_rating": rating_to_stars(doc.student_rating),
		"student_feedback": doc.student_feedback,
		"can_comment": doc.status != "Closed",
		"can_reopen": doc.reopen_window_open(),
		"can_rate": doc.status in ("Resolved", "Closed"),
		"attachments": attachments,
		"timeline": doc.public_updates(),
	}


def raise_grievance(student: str, data: dict, user: str, attachments: list[str] | None = None) -> str:
	assert_can_access_student(student, user)
	data = data or {}
	attachments = [url for url in (attachments or []) if url]
	if len(attachments) > MAX_GRIEVANCE_ATTACHMENTS:
		frappe.throw(_("Please attach at most {0} files.").format(MAX_GRIEVANCE_ATTACHMENTS))
	files = get_own_unattached_files(attachments, user)

	doc = frappe.new_doc(GRIEVANCE)
	doc.update(
		{
			"student": student,
			"category": data.get("category"),
			"subject": (data.get("subject") or "").strip()[:140],
			"description": (data.get("description") or "").strip(),
			"priority": data.get("priority") or "Medium",
			"is_confidential": cint(data.get("is_confidential")),
			"raised_by_user": user,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()

	for file_name in files:
		frappe.db.set_value(
			"File",
			file_name,
			{"attached_to_doctype": GRIEVANCE, "attached_to_name": doc.name, "attached_to_field": None},
			update_modified=False,
		)
	return doc.name


def get_own_unattached_files(file_urls: list[str], user: str) -> list[str]:
	"""File records for uploads made by this user that are private and not yet attached."""
	names = []
	for url in dict.fromkeys(file_urls):
		name = frappe.db.get_value(
			"File",
			{
				"file_url": url,
				"owner": user,
				"is_private": 1,
				"is_folder": 0,
				"attached_to_name": ["is", "not set"],
			},
			"name",
		)
		if not name:
			frappe.throw(
				_("Attachment {0} was not found. Please upload it again.").format(url.rsplit("/", 1)[-1])
			)
		names.append(name)
	return names


def add_grievance_comment(name: str, comment: str, user: str) -> None:
	doc = frappe.get_doc(GRIEVANCE, name, for_update=True)
	if is_staff_user(user):
		doc.add_staff_update(comment, user)
		return
	assert_can_access_student(doc.student, user)
	doc.comment_by(comment, user)


def reopen_grievance(name: str, reason: str, user: str) -> None:
	doc = frappe.get_doc(GRIEVANCE, name, for_update=True)
	assert_can_access_student(doc.student, user)
	doc.reopen_by(reason, user)


def rate_grievance(name: str, rating: int, feedback: str | None, user: str) -> None:
	doc = frappe.get_doc(GRIEVANCE, name, for_update=True)
	assert_can_access_student(doc.student, user)
	doc.rate_by(rating, feedback, user)


# ---------------------------------------------------------------------- feedback


def submit_feedback(student: str, data: dict) -> str:
	"""Ratings in ``data`` are 1-5 stars (0 or empty when not rated)."""
	data = data or {}
	doc = frappe.new_doc(FEEDBACK)
	doc.update(
		{
			"student": student,
			"feedback_type": data.get("feedback_type"),
			"period": data.get("period"),
			"comments": (data.get("comments") or "").strip()[:2000] or None,
			"is_anonymous": cint(data.get("is_anonymous")),
		}
	)
	for fieldname in ("overall", "food_quality", "cleanliness", "staff_behaviour", "facilities"):
		if data.get(fieldname) not in (None, ""):
			doc.set(fieldname, stars_to_rating(data.get(fieldname)))
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


# ---------------------------------------------------------------------- bonafide certificates


def list_certificates(student: str) -> list[dict]:
	rows = frappe.get_all(
		BONAFIDE,
		filters={"student": student},
		fields=[
			"name",
			"purpose",
			"purpose_details",
			"status",
			"creation",
			"approved_on",
			"rejection_reason",
			"certificate_number",
			"issue_date",
			"valid_till",
		],
		order_by="creation desc",
		limit=LIST_LIMIT,
	)
	today = frappe.utils.getdate()
	for row in rows:
		row["can_download"] = row.status in ("Approved", "Issued")
		row["is_expired"] = bool(row.valid_till) and frappe.utils.getdate(row.valid_till) < today
	return rows


def request_bonafide(student: str, purpose: str, purpose_details: str | None) -> str:
	doc = frappe.new_doc(BONAFIDE)
	doc.update({"student": student, "purpose": purpose, "purpose_details": purpose_details})
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def get_bonafide_pdf(name: str) -> bytes:
	status = frappe.db.get_value(BONAFIDE, name, "status")
	if not status:
		frappe.throw(_("Certificate request {0} was not found.").format(name), frappe.DoesNotExistError)
	if status not in ("Approved", "Issued"):
		frappe.throw(_("The certificate can be downloaded once the request is approved."))

	# The portal caller has no DocType permissions; the API has already checked ownership.
	previous = frappe.flags.ignore_print_permissions
	frappe.flags.ignore_print_permissions = True
	try:
		return frappe.get_print(
			BONAFIDE, name, print_format=BONAFIDE_PRINT_FORMAT, as_pdf=True, no_letterhead=1
		)
	finally:
		frappe.flags.ignore_print_permissions = previous
