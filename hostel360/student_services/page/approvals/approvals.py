"""Approvals inbox: everything waiting on the signed-in approver, in one place.

Each section is a single query. Sections the user cannot act on are left out rather than
shown empty, so a mess manager sees mess work and a deputy warden sees their hostels.
"""

import frappe
from frappe import _

from hostel360.setup.roles import ADMIN_ROLES, DEPUTY_WARDEN_ROLE, MESS_MANAGER_ROLE, WARDEN_ROLE
from hostel360.student_services.utils import GLOBAL_APPROVER_ROLES, get_user_hostels, staff_covers_student

SECTION_LIMIT = 200
ALL_HOSTELS = "*"
GRIEVANCE_OPEN_STATUSES = ("Open", "In Progress", "Escalated", "Reopened")

LEAVE = "Home Visit Leave"
REDUCTION = "Mess Reduction Application"
BONAFIDE = "Bonafide Certificate Request"
INDENT = "Mess Indent"
GRIEVANCE = "Hostel Grievance"


@frappe.whitelist()
def get_inbox() -> dict:
	user = frappe.session.user
	sections = {}
	for key, loader in (
		("leaves", pending_leaves),
		("mess_reductions", pending_mess_reductions),
		("certificates", pending_certificates),
		("indents", pending_indents),
		("grievances", attention_grievances),
	):
		rows = loader(user)
		if rows is not None:
			sections[key] = rows
	return {
		"sections": sections,
		"counts": {key: len(rows) for key, rows in sections.items()},
		"limit": SECTION_LIMIT,
		# Read-only roles (a mess manager, say) see grievances but act only on those assigned to them.
		"can_act_on_grievances": bool(frappe.has_permission(GRIEVANCE, "write", user=user)),
	}


@frappe.whitelist(methods=["POST"])
def decide(doctype: str, name: str, action: str, remarks: str | None = None) -> dict:
	"""Approve or reject one item from the inbox."""
	if action not in ("approve", "reject"):
		frappe.throw(_("Unknown action."))
	approve = action == "approve"
	user = frappe.session.user
	remarks = (remarks or "").strip() or None

	if doctype == LEAVE:
		from hostel360.student_services.services import warden_decision

		warden_decision(name, approve, remarks, user)
	elif doctype == BONAFIDE:
		doc = frappe.get_doc(BONAFIDE, name, for_update=True)
		if approve:
			doc.approve_by(user)
		else:
			doc.reject_by(remarks, user)
	elif doctype == REDUCTION:
		from hostel360.hostel_mess.services import decide_mess_reduction

		decide_mess_reduction(name, approve, remarks, user)
	elif doctype == INDENT:
		doc = frappe.get_doc(INDENT, name, for_update=True)
		doc.check_permission("read")
		method = getattr(doc, "approve" if approve else "reject", None)
		if not callable(method):
			frappe.throw(_("Indents cannot be decided from here on this site."))
		method(remarks=remarks)
	else:
		frappe.throw(_("{0} cannot be decided from the approvals inbox.").format(doctype))

	return {"ok": True, "status": frappe.db.get_value(doctype, name, "status")}


@frappe.whitelist(methods=["POST"])
def act_on_grievance(name: str, action: str, message: str | None = None) -> dict:
	"""Take ownership of, or resolve, a grievance that needs attention."""
	doc = frappe.get_doc(GRIEVANCE, name, for_update=True)
	doc.check_permission("read")
	user = frappe.session.user
	if action == "take":
		doc.assign_to_user(user, user, message)
	elif action == "resolve":
		doc.resolve_by(message, user)
	else:
		frappe.throw(_("Unknown action."))
	return {"ok": True, "status": doc.status}


# ---------------------------------------------------------------------- sections


def approver_hostels(user: str):
	"""ALL_HOSTELS, the set of hostels a deputy warden covers, or None for non-approvers."""
	if user == "Administrator":
		return ALL_HOSTELS
	roles = set(frappe.get_roles(user))
	if roles & GLOBAL_APPROVER_ROLES:
		return ALL_HOSTELS
	if DEPUTY_WARDEN_ROLE in roles:
		return get_user_hostels(user)
	return None


def scoped_filters(user: str, filters: dict) -> dict | None:
	hostels = approver_hostels(user)
	if hostels is None:
		return None
	if hostels != ALL_HOSTELS:
		if not hostels:
			return {**filters, "name": ["is", "not set"]}
		filters = {**filters, "hostel": ["in", sorted(hostels)]}
	return filters


def covered_rows(user: str, rows: list) -> list:
	"""Rows of students the user looks after, for deputies assigned to particular blocks."""
	if approver_hostels(user) == ALL_HOSTELS:
		return rows
	return [row for row in rows if staff_covers_student(user, row.student, row.hostel)]


def pending_leaves(user: str):
	filters = scoped_filters(user, {"status": "Pending Warden Approval"})
	if filters is None:
		return None
	rows = frappe.get_all(
		LEAVE,
		filters=filters,
		fields=[
			"name",
			"student",
			"student_name",
			"hostel",
			"room",
			"from_datetime",
			"to_datetime",
			"number_of_days",
			"destination",
			"reason",
			"contact_during_leave",
			"applied_by",
			"parent_approval_status",
			"parent_guardian_name",
			"parent_remarks",
			"apply_mess_reduction",
			"mess_reduction_days",
			"creation",
		],
		order_by="from_datetime asc",
		limit=SECTION_LIMIT,
	)
	return covered_rows(user, rows)


def pending_certificates(user: str):
	filters = scoped_filters(user, {"status": "Pending"})
	if filters is None:
		return None
	rows = frappe.get_all(
		BONAFIDE,
		filters=filters,
		fields=[
			"name",
			"student",
			"student_name",
			"hostel",
			"program",
			"year_of_study",
			"purpose",
			"purpose_details",
			"creation",
		],
		order_by="creation asc",
		limit=SECTION_LIMIT,
	)
	return covered_rows(user, rows)


def pending_mess_reductions(user: str):
	if not frappe.db.exists("DocType", REDUCTION):
		return None
	try:
		from hostel360.hostel_mess.utils import get_reduction_decider_roles

		decider_roles = get_reduction_decider_roles()
	except ImportError:
		decider_roles = set(ADMIN_ROLES) | {WARDEN_ROLE}
	if user != "Administrator" and not set(frappe.get_roles(user)) & set(decider_roles):
		return None
	# get_list applies role and hostel User Permissions, matching what the user may decide.
	return frappe.get_list(
		REDUCTION,
		filters={"status": "Pending"},
		fields=existing_fields(
			REDUCTION,
			[
				"name",
				"student",
				"student_name",
				"hostel",
				"mess",
				"from_date",
				"to_date",
				"number_of_days",
				"reason",
				"description",
				"home_visit_leave",
				"creation",
			],
		),
		order_by="from_date asc",
		limit=SECTION_LIMIT,
	)


def pending_indents(user: str):
	if not frappe.db.exists("DocType", INDENT):
		return None
	try:
		from hostel360.hostel_mess.doctype.mess_indent.mess_indent import (
			INDENT_APPROVER_ROLES,
			INDENT_SENIOR_APPROVER_ROLES,
		)
	except ImportError:
		INDENT_APPROVER_ROLES = set(ADMIN_ROLES) | {MESS_MANAGER_ROLE, WARDEN_ROLE}
		INDENT_SENIOR_APPROVER_ROLES = set(ADMIN_ROLES) | {WARDEN_ROLE}
	roles = set(frappe.get_roles(user))
	if user != "Administrator" and not roles & set(INDENT_APPROVER_ROLES):
		return None
	if not frappe.has_permission(INDENT, "read", user=user):
		return None

	filters = {"status": "Pending Approval", "docstatus": 1}
	if user != "Administrator" and not roles & set(INDENT_SENIOR_APPROVER_ROLES):
		# Whoever raised an indent does not also approve it; the Mess Indent refuses that, so
		# such indents wait for someone else instead of offering buttons that would fail.
		filters["owner"] = ["!=", user]
		if frappe.get_meta(INDENT).has_field("requested_by"):
			filters["requested_by"] = ["!=", user]
	return frappe.get_list(
		INDENT,
		filters=filters,
		fields=existing_fields(
			INDENT,
			[
				"name",
				"mess",
				"posting_date",
				"required_by",
				"total_estimated_amount",
				"remarks",
				"owner",
				"requested_by",
				"creation",
			],
		),
		order_by="required_by asc",
		limit=SECTION_LIMIT,
	)


def attention_grievances(user: str):
	if not frappe.has_permission(GRIEVANCE, "read", user=user):
		return None
	# Permission query conditions keep confidential grievances out unless the user may see them.
	return frappe.get_list(
		GRIEVANCE,
		filters={"status": ["in", GRIEVANCE_OPEN_STATUSES]},
		or_filters={"status": "Escalated", "priority": "Urgent"},
		fields=[
			"name",
			"subject",
			"category",
			"priority",
			"status",
			"is_confidential",
			"hostel",
			"student_name",
			"assigned_to",
			"escalation_level",
			"sla_due_by",
			"creation",
		],
		order_by="escalation_level desc, sla_due_by asc",
		limit=SECTION_LIMIT,
	)


def existing_fields(doctype: str, fields: list[str]) -> list[str]:
	meta = frappe.get_meta(doctype)
	standard = {"name", "owner", "creation", "modified", "docstatus"}
	return [f for f in fields if f in standard or meta.has_field(f)]
