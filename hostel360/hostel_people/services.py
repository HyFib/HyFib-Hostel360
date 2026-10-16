"""Student and guardian services used by the portal API and other modules.

The portal API authenticates the caller and checks ownership before calling these
functions; they also re-check access where they read or change a student's record, so
a mistake in one caller cannot expose another student's data.

This module also answers "which hostel does this student belong to" and "which staff
look after this student" for the other modules (approvals, notifications, records that
copy the student's hostel).
"""

import re
import secrets
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import now_datetime, strip_html, validate_email_address

from hostel360.setup.roles import (
	ADMIN_ROLE,
	DESIGNATION_ROLES,
	HOSTEL_SCOPED_DESIGNATIONS,
	PARENT_ROLE,
	STAFF_ROLES,
	STUDENT_ROLE,
	WARDEN_ROLE,
)
from hostel360.utils.context import assert_can_access_student, get_guardian_for_user, has_any_role
from hostel360.utils.validation import mask_mobile, normalize_mobile, validate_pincode

# Fields a student may change while confirming the profile in the portal.
PROFILE_EDITABLE_FIELDS = (
	"email",
	"address_line1",
	"address_line2",
	"city",
	"district",
	"state",
	"pincode",
	"emergency_contact_name",
	"emergency_contact_phone",
	"blood_group",
)

PLACEHOLDER_EMAIL_DOMAIN = "portal.hostel360.invalid"

PORTAL_DOCTYPES = {
	"Hostel Student": STUDENT_ROLE,
	"Hostel Guardian": PARENT_ROLE,
}


# ---------------------------------------------------------------------- summary


def get_student_summary(student: str) -> dict:
	"""Profile, current room and guardians of a student, with mobile numbers masked."""
	assert_can_access_student(student)
	doc = frappe.get_doc("Hostel Student", student)

	room = None
	if doc.current_allotment:
		allotment = frappe.db.get_value(
			"Room Allotment",
			doc.current_allotment,
			["name", "from_date", "to_date", "allotment_type"],
			as_dict=True,
		)
		room = {
			"allotment": allotment.name,
			"hostel": doc.current_hostel,
			"hostel_name": frappe.get_cached_value("Hostel", doc.current_hostel, "hostel_name"),
			"block": None,
			"block_name": None,
			"floor_label": None,
			"room": doc.current_room,
			"room_number": None,
			"room_type": None,
			"bed": doc.current_bed,
			"bed_label": frappe.db.get_value("Hostel Bed", doc.current_bed, "bed_label"),
			"from_date": allotment.from_date,
			"to_date": allotment.to_date,
		}
		room_row = frappe.db.get_value(
			"Hostel Room", doc.current_room, ["room_number", "room_type", "block", "floor"], as_dict=True
		)
		if room_row:
			room.update(room_number=room_row.room_number, room_type=room_row.room_type, block=room_row.block)
			if room_row.block:
				room["block_name"] = frappe.db.get_value("Hostel Block", room_row.block, "block_name")
			if room_row.floor:
				room["floor_label"] = frappe.db.get_value("Hostel Floor", room_row.floor, "floor_label")

	guardians = []
	for row in doc.guardians:
		guardians.append(
			{
				"guardian": row.guardian,
				"guardian_name": row.guardian_name,
				"relation": row.relation,
				"mobile_masked": mask_mobile(row.mobile_number),
				"is_primary": row.is_primary,
				"can_approve_leave": row.can_approve_leave,
			}
		)

	return {
		"name": doc.name,
		"register_number": doc.register_number,
		"roll_number": doc.roll_number,
		"first_name": doc.first_name,
		"last_name": doc.last_name,
		"full_name": doc.full_name,
		"gender": doc.gender,
		"date_of_birth": doc.date_of_birth,
		"blood_group": doc.blood_group,
		"photo": doc.photo,
		"program": doc.program,
		"department": doc.department,
		"year_of_study": doc.year_of_study,
		"admission_year": doc.admission_year,
		"academic_year": doc.academic_year,
		"mobile_masked": mask_mobile(doc.mobile_number),
		"email": doc.email,
		"address_line1": doc.address_line1,
		"address_line2": doc.address_line2,
		"city": doc.city,
		"district": doc.district,
		"state": doc.state,
		"pincode": doc.pincode,
		"emergency_contact_name": doc.emergency_contact_name,
		"emergency_contact_phone": doc.emergency_contact_phone,
		"status": doc.status,
		"validation_status": doc.validation_status,
		"profile_confirmed": doc.profile_confirmed,
		"privacy_consent_version": doc.privacy_consent_version,
		"mess_enrolled": doc.mess_enrolled,
		"room": room,
		"guardians": guardians,
		"editable_fields": list(PROFILE_EDITABLE_FIELDS),
	}


# ---------------------------------------------------------------------- profile


def confirm_profile(student: str, updates: dict) -> None:
	"""Apply the student's corrections to contact details and mark the profile confirmed.

	Only ``PROFILE_EDITABLE_FIELDS`` are applied; identity, academic and residence fields
	are maintained by the hostel office and anything else in ``updates`` is ignored.
	"""
	assert_can_access_student(student)
	updates = frappe.parse_json(updates) if isinstance(updates, str) else (updates or {})
	if not isinstance(updates, dict):
		frappe.throw(_("Profile updates must be a set of field values."))

	doc = frappe.get_doc("Hostel Student", student)
	blood_groups = [option for option in (doc.meta.get_options("blood_group") or "").split("\n") if option]

	for fieldname in PROFILE_EDITABLE_FIELDS:
		if fieldname not in updates:
			continue
		value = updates.get(fieldname)
		value = "" if value is None else strip_html(str(value)).strip()
		if len(value) > 140:
			frappe.throw(_("{0} is too long.").format(_(doc.meta.get_label(fieldname))))

		if fieldname == "email" and value:
			value = value.lower()
			validate_email_address(value, throw=True)
		elif fieldname == "pincode":
			validate_pincode(value)
		elif fieldname == "emergency_contact_phone":
			value = normalize_mobile(value) or ""
		elif fieldname == "blood_group" and value and value not in blood_groups:
			frappe.throw(_("{0} is not a valid blood group.").format(value))

		doc.set(fieldname, value or None)

	doc.profile_confirmed = 1
	doc.flags.ignore_permissions = True
	doc.flags.allow_system_fields = ("profile_confirmed",)
	doc.save()
	doc.add_comment("Info", _("Profile confirmed by {0}").format(frappe.session.user))


def record_privacy_consent(doctype: str, name: str, version: str) -> None:
	"""Record that a student or guardian accepted the current privacy notice."""
	if doctype not in PORTAL_DOCTYPES:
		frappe.throw(_("Consent can only be recorded for students and guardians."))

	current_version = frappe.db.get_single_value("Hostel Settings", "privacy_notice_version")
	if not version or str(version).strip() != (current_version or "").strip():
		frappe.throw(
			_("The privacy notice has changed. Please read version {0} and accept it again.").format(
				current_version
			)
		)

	if doctype == "Hostel Student":
		assert_can_access_student(name)
	else:
		user = frappe.session.user
		if get_guardian_for_user(user) != name and not has_any_role(STAFF_ROLES, user):
			frappe.throw(
				_("You are not permitted to record consent for this guardian."), frappe.PermissionError
			)

	doc = frappe.get_doc(doctype, name)
	if doc.privacy_consent_version == current_version:
		return
	doc.privacy_consent_version = current_version
	doc.privacy_consent_on = now_datetime()
	doc.flags.ignore_permissions = True
	doc.flags.allow_system_fields = ("privacy_consent_version", "privacy_consent_on")
	doc.save()
	doc.add_comment(
		"Info",
		_("Accepted privacy notice version {0} (signed in as {1})").format(
			current_version, frappe.session.user
		),
	)


# ---------------------------------------------------------------------- portal users


def ensure_portal_user(doctype: str, name: str) -> str:
	"""Return the portal (Website) user of a student or guardian, creating it when missing.

	Callers check who may trigger this (desk staff, or the OTP login flow after the
	mobile number has been verified). The user gets a random password nobody knows, so
	the only way in is an OTP to the registered mobile. ``User.mobile_no`` is deliberately
	left empty: it is unique in Frappe and a student may share a number with a guardian.
	"""
	role = PORTAL_DOCTYPES.get(doctype)
	if not role:
		frappe.throw(_("Portal users can only be created for students and guardians."))

	# Serialise concurrent calls for the same record (two OTP logins at once).
	linked_user = frappe.db.get_value(doctype, name, "user", for_update=True)
	if linked_user is None and not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} does not exist.").format(_(doctype), name), frappe.DoesNotExistError)

	if linked_user and frappe.db.exists("User", linked_user):
		_ensure_role(linked_user, role)
		return linked_user

	doc = frappe.get_doc(doctype, name)
	email = _portal_email(doctype, doc)
	user_name = frappe.db.get_value("User", {"email": email}, "name") or frappe.db.exists("User", email)

	if not user_name:
		first_name, last_name = _person_names(doctype, doc)
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"last_name": last_name,
				"user_type": "Website User",
				"send_welcome_email": 0,
				"enabled": 1,
				"roles": [{"role": role}],
			}
		)
		user.flags.ignore_permissions = True
		user.flags.no_welcome_mail = True
		try:
			user.insert()
			user_name = user.name
		except frappe.DuplicateEntryError:
			user_name = frappe.db.get_value("User", {"email": email}, "name")
		_set_unusable_password(user_name)
	else:
		_ensure_role(user_name, role)

	frappe.db.set_value(doctype, name, "user", user_name, update_modified=False)
	return user_name


def _portal_email(doctype: str, doc) -> str:
	"""The person's own e-mail when it is free, otherwise a placeholder that never receives mail."""
	email = (doc.get("email") or "").strip().lower()
	if email and _email_available_for(email, doctype, doc.name):
		return email
	prefix = "student" if doctype == "Hostel Student" else "guardian"
	slug = re.sub(r"[^a-z0-9]+", "-", f"{prefix}-{doc.name}".lower()).strip("-")
	return f"{slug}@{PLACEHOLDER_EMAIL_DOMAIN}"


def _email_available_for(email: str, doctype: str, name: str) -> bool:
	"""True when no user has this e-mail, or the user is an unclaimed portal account."""
	user = frappe.db.get_value("User", {"email": email}, ["name", "user_type"], as_dict=True)
	if not user:
		return True
	if user.user_type != "Website User":
		# Never hand a staff account to a portal identity.
		return False
	for other_doctype in PORTAL_DOCTYPES:
		owner = frappe.db.get_value(other_doctype, {"user": user.name}, "name")
		if owner and not (other_doctype == doctype and owner == name):
			return False
	return True


def _person_names(doctype: str, doc) -> tuple[str, str]:
	if doctype == "Hostel Student":
		return doc.first_name, doc.last_name or ""
	first, _sep, last = (doc.guardian_name or "").partition(" ")
	return first or doc.guardian_name, last


def _ensure_role(user: str, role: str) -> None:
	if role in frappe.get_roles(user):
		return
	user_doc = frappe.get_doc("User", user)
	user_doc.append("roles", {"role": role})
	user_doc.flags.ignore_permissions = True
	user_doc.save()


def _set_unusable_password(user: str) -> None:
	from frappe.utils.password import update_password

	update_password(user, secrets.token_urlsafe(48))


def can_unlock_sign_in(user: str | None = None) -> bool:
	"""May the user lift a portal sign-in lock (``hostel360.api.auth.unlock_sign_in``)?

	The student and guardian forms show their Unlock Sign-in button from this, so the
	button appears exactly for the roles the endpoint accepts.
	"""
	try:
		from hostel360.api.auth import UNLOCK_ROLES
	except ImportError:
		return False
	return has_any_role(UNLOCK_ROLES, user)


# ---------------------------------------------------------------------- hostel of a student


def get_student_hostel(student: str) -> str | None:
	"""The hostel a student lives in or, after vacating, last lived in.

	Records that copy a student's hostel (invoices, receipts, leave, grievances...) must use
	this rather than ``current_hostel`` alone: an empty hostel makes a record visible to
	every hostel's staff, because Frappe ignores User Permissions on empty links.
	Returns None for a student who has never been allotted a bed.
	"""
	if not student:
		return None
	row = frappe.db.get_value("Hostel Student", student, ["current_hostel", "last_hostel"], as_dict=True)
	if not row:
		return None
	return row.current_hostel or row.last_hostel or None


def get_student_block(student: str) -> str | None:
	"""Hostel Block of the room the student lives in now (None when not in a room or block)."""
	room = frappe.db.get_value("Hostel Student", student, "current_room") if student else None
	return frappe.db.get_value("Hostel Room", room, "block") if room else None


# ---------------------------------------------------------------------- staff scope
#
# A staff member's scope says which students they look after:
#   None                      every hostel (wardens, administrators, unrestricted roles)
#   {hostel: None}            the whole of that hostel
#   {hostel: {block, ...}}    only students living in those blocks of that hostel
# Hostel coverage comes from the user's Hostel User Permissions, which is also what
# Frappe enforces in Desk. Block coverage comes from the assignment rows of their active
# Hostel Staff record; a row without a block covers the whole hostel.

# Holders of these roles act for every hostel and block.
UNSCOPED_ROLES = frozenset({ADMIN_ROLE, WARDEN_ROLE, "System Manager"})
# Roles of the designations that are restricted to their assigned hostels.
SCOPED_ROLES = frozenset(
	role for designation in HOSTEL_SCOPED_DESIGNATIONS for role in DESIGNATION_ROLES.get(designation, [])
)


def get_staff_scope(user: str | None = None) -> dict | None:
	"""The scope of one user, as described above. ``{}`` means the user covers no student.

	Roles and User Permissions come from Frappe's caches, so for wardens and other
	unscoped users this costs no query; scoped staff cost one (their assignments).
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	from frappe.permissions import get_user_permissions

	hostels = {
		row.get("doc")
		for row in get_user_permissions(user).get("Hostel") or []
		if _restricts_students(row.get("applicable_for"))
	}
	return _build_scope(set(frappe.get_roles(user)), hostels, lambda: _assigned_blocks([user]).get(user, {}))


def staff_covers(user: str | None, hostel: str | None, block: str | None = None) -> bool:
	"""Does the user look after students of this hostel (and block, when given)?"""
	return scope_covers(get_staff_scope(user), hostel, block)


def staff_covers_student(user: str | None, student: str) -> bool:
	"""Does the user look after this student?

	True for unscoped roles (Hostel Warden, Hostel Administrator, System Manager). Scoped
	staff need an assignment for the student's hostel whose block is empty or is the block
	the student lives in now. A vacated student counts under their last hostel; a student
	who never had a bed is not covered by scoped staff.
	"""
	scope = get_staff_scope(user)
	if scope is None:
		return True
	row = frappe.db.get_value(
		"Hostel Student", student, ["current_hostel", "last_hostel", "current_room"], as_dict=True
	)
	if not row:
		return False
	block = None
	if row.current_hostel and row.current_room:
		block = frappe.db.get_value("Hostel Room", row.current_room, "block")
	return scope_covers(scope, row.current_hostel or row.last_hostel, block)


def get_scoped_staff_users(role: str, hostel: str | None, block: str | None = None) -> list[str]:
	"""Enabled desk users holding ``role`` who look after ``hostel`` (and ``block``).

	Staff assigned to particular blocks of the hostel are included only when ``block`` is
	one of them. Holders of unscoped roles are always included.
	"""
	if not role:
		return []
	holders = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent", distinct=True
	)
	holders = [user for user in holders if user not in ("Administrator", "Guest")]
	if not holders:
		return []
	holders = frappe.get_all(
		"User",
		filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"},
		pluck="name",
		order_by="name asc",
	)
	scopes = _scopes_for(holders)
	return [user for user in holders if scope_covers(scopes[user], hostel, block)]


def scope_covers(scope: dict | None, hostel: str | None, block: str | None = None) -> bool:
	if scope is None:
		return True
	if not hostel or hostel not in scope:
		return False
	blocks = scope[hostel]
	return blocks is None or (block in blocks)


def block_restrictions(scope: dict | None) -> dict[str, frozenset[str]]:
	"""Only the hostels in which the scope is limited to particular blocks."""
	if not scope:
		return {}
	return {hostel: blocks for hostel, blocks in scope.items() if blocks is not None}


def _scopes_for(users: list[str]) -> dict[str, dict | None]:
	"""Scopes of many users with a fixed number of queries (for recipient lists)."""
	users = [user for user in dict.fromkeys(users) if user]
	scopes: dict[str, dict | None] = {}
	if "Administrator" in users:
		scopes["Administrator"] = None
	users = [user for user in users if user != "Administrator"]
	if not users:
		return scopes

	roles_of = defaultdict(set)
	for row in frappe.get_all(
		"Has Role", filters={"parent": ["in", users], "parenttype": "User"}, fields=["parent", "role"]
	):
		roles_of[row.parent].add(row.role)

	hostels_of = defaultdict(set)
	for row in frappe.get_all(
		"User Permission",
		filters={"user": ["in", users], "allow": "Hostel"},
		fields=["user", "for_value", "apply_to_all_doctypes", "applicable_for"],
	):
		if row.apply_to_all_doctypes or _restricts_students(row.applicable_for):
			hostels_of[row.user].add(row.for_value)

	blocks: dict | None = None

	def blocks_of(user):
		nonlocal blocks
		if blocks is None:
			blocks = _assigned_blocks(users)
		return blocks.get(user, {})

	for user in users:
		scopes[user] = _build_scope(
			roles_of[user], hostels_of.get(user, set()), lambda user=user: blocks_of(user)
		)
	return scopes


def _build_scope(roles: set[str], hostels: set[str], assigned_blocks) -> dict | None:
	if roles & UNSCOPED_ROLES:
		return None
	if not roles & (STAFF_ROLES | SCOPED_ROLES):
		# Portal users and accounts without a hostel role look after nobody.
		return {}
	if not hostels:
		# Mess managers and auditors work across hostels. A hostel-scoped role without any
		# hostel assigned is a misconfiguration and must not mean "everywhere".
		return {} if roles & SCOPED_ROLES else None
	assigned = assigned_blocks()
	return {
		hostel: None if not assigned.get(hostel) or "" in assigned[hostel] else frozenset(assigned[hostel])
		for hostel in hostels
	}


def _restricts_students(applicable_for: str | None) -> bool:
	# Permissions limited to some other DocType do not restrict which students a user sees.
	return not applicable_for or applicable_for == "Hostel Student"


def _assigned_blocks(users: list[str]) -> dict[str, dict[str, set[str]]]:
	"""{user: {hostel: {block or ""}}} from the active Hostel Staff records of the users."""
	staff = frappe.qb.DocType("Hostel Staff")
	assignment = frappe.qb.DocType("Hostel Staff Assignment")
	rows = (
		frappe.qb.from_(assignment)
		.join(staff)
		.on(assignment.parent == staff.name)
		.select(staff.user, assignment.hostel, assignment.block)
		.where(assignment.parenttype == "Hostel Staff")
		.where(staff.is_active == 1)
		.where(staff.user.isin(users))
	).run(as_dict=True)
	result = defaultdict(lambda: defaultdict(set))
	for row in rows:
		result[row.user][row.hostel].add(row.block or "")
	return result
