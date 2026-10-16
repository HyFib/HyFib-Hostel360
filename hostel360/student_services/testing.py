"""Fixtures for Student Services tests.

Every helper creates fresh records with unique values so tests never depend on each
other or on data from other modules' tests.
"""

import random
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, getdate, now_datetime

from hostel360.setup.roles import PARENT_ROLE, STUDENT_ROLE


def uid(length: int = 6) -> str:
	return frappe.generate_hash(length=length).upper()


def unique_mobile() -> str:
	while True:
		mobile = str(random.choice("6789")) + "".join(random.choices("0123456789", k=9))
		if not frappe.db.exists("Hostel Student", {"mobile_number": mobile}) and not frappe.db.exists(
			"Hostel Guardian", {"mobile_number": mobile}
		):
			return mobile


def make_hostel(gender: str = "Male") -> str:
	code = "T" + uid(5)
	doc = frappe.get_doc(
		{
			"doctype": "Hostel",
			"hostel_name": f"Test Hostel {code}",
			"hostel_code": code,
			"gender": gender,
			"is_active": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def make_user(roles: list[str], first_name: str = "Test", website_user: bool = False) -> str:
	email = f"h360-{uid(8).lower()}@example.com"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"enabled": 1,
			"first_name": first_name,
			"last_name": uid(4),
			"send_welcome_email": 0,
			"user_type": "Website User" if website_user else "System User",
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.no_welcome_mail = True
	# Test classes create many users inside one transaction; skip Frappe's sign-up throttle.
	# (Defaults are not applied while importing, hence the explicit "enabled" above.)
	in_import, frappe.flags.in_import = frappe.flags.in_import, True
	try:
		user.insert(ignore_permissions=True)
	finally:
		frappe.flags.in_import = in_import
	return user.name


def scope_to_hostel(user: str, hostel: str) -> None:
	from frappe.permissions import add_user_permission

	add_user_permission("Hostel", hostel, user, ignore_permissions=True)


def make_student(
	hostel: str | None = None,
	status: str = "Active",
	validation_status: str = "Validated",
	with_user: bool = True,
	first_name: str = "Rajesh",
	gender: str = "Male",
) -> frappe._dict:
	register_number = "TS" + uid(8)
	doc = frappe.get_doc(
		{
			"doctype": "Hostel Student",
			"register_number": register_number,
			"first_name": first_name,
			"last_name": "Kannan",
			"gender": gender,
			"year_of_study": "2",
			"mobile_number": unique_mobile(),
			"status": "Active",
		}
	)
	doc.insert(ignore_permissions=True)
	user = make_user([STUDENT_ROLE], first_name=first_name, website_user=True) if with_user else None
	# Allotment and validation belong to other modules; set their outcome directly.
	frappe.db.set_value(
		"Hostel Student",
		doc.name,
		{
			"current_hostel": hostel,
			"status": status,
			"validation_status": validation_status,
			"user": user,
		},
		update_modified=False,
	)
	return frappe._dict(name=doc.name, user=user, hostel=hostel, full_name=f"{first_name} Kannan")


def make_guardian(
	student: str, can_approve_leave: int = 1, relation: str = "Father", with_user: bool = True
) -> frappe._dict:
	guardian = frappe.get_doc(
		{
			"doctype": "Hostel Guardian",
			"guardian_name": f"Guardian {uid(4)}",
			"relation": relation,
			"mobile_number": unique_mobile(),
		}
	)
	guardian.insert(ignore_permissions=True)
	user = make_user([PARENT_ROLE], first_name="Parent", website_user=True) if with_user else None
	if user:
		frappe.db.set_value("Hostel Guardian", guardian.name, "user", user, update_modified=False)

	student_doc = frappe.get_doc("Hostel Student", student)
	student_doc.append(
		"guardians",
		{
			"guardian": guardian.name,
			"relation": relation,
			"is_primary": 0 if student_doc.guardians else 1,
			"can_approve_leave": can_approve_leave,
		},
	)
	student_doc.flags.ignore_permissions = True
	student_doc.save()
	return frappe._dict(name=guardian.name, user=user, guardian_name=guardian.guardian_name)


def make_block_room(hostel: str) -> frappe._dict:
	"""A block of the hostel with one room in it."""
	block = frappe.get_doc(
		{"doctype": "Hostel Block", "hostel": hostel, "block_name": f"Block {uid(3)}", "block_code": uid(3)}
	).insert(ignore_permissions=True)
	room = frappe.get_doc(
		{
			"doctype": "Hostel Room",
			"hostel": hostel,
			"block": block.name,
			"room_number": "R" + uid(4),
			"room_type": "Double",
			"capacity": 2,
		}
	).insert(ignore_permissions=True)
	return frappe._dict(block=block.name, room=room.name)


def put_in_room(student: str, room: str) -> None:
	# Allotment belongs to Hostel People; only its outcome matters here.
	frappe.db.set_value("Hostel Student", student, "current_room", room, update_modified=False)


def make_block_deputy(hostel: str, blocks: list[str]) -> str:
	"""A deputy warden assigned, through a Hostel Staff record, to some blocks of a hostel."""
	return make_scoped_staff("Deputy Warden", hostel, blocks)


def make_scoped_staff(designation: str, hostel: str, blocks: list[str] | None = None) -> str:
	"""A desk user for a Hostel Staff record of ``designation``, posted to the hostel, or to
	some of its blocks when ``blocks`` are given. Returns the user."""
	suffix = uid(8).lower()
	staff = frappe.get_doc(
		{
			"doctype": "Hostel Staff",
			"staff_name": f"{designation} {suffix}",
			"designation": designation,
			"email": f"h360-staff-{suffix}@example.com",
			"mobile_number": unique_mobile(),
			"create_user": 1,
			"send_welcome_email": 0,
			"is_active": 1,
			"assignments": [{"hostel": hostel, "block": block} for block in blocks or [None]],
		}
	)
	staff.insert(ignore_permissions=True)
	return frappe.db.get_value("Hostel Staff", staff.name, "user")


def make_linked_reduction(leave: str, from_date=None, to_date=None, status: str = "Approved") -> str:
	"""A Mess Reduction Application raised for a leave, already in ``status``."""
	values = frappe.db.get_value(
		"Home Visit Leave", leave, ["student", "from_datetime", "to_datetime"], as_dict=True
	)
	frappe.flags.hostel360_migration = True
	try:
		doc = frappe.get_doc(
			{
				"doctype": "Mess Reduction Application",
				"student": values.student,
				"from_date": getdate(from_date or values.from_datetime),
				"to_date": getdate(to_date or values.to_datetime),
				"reason": "Home Visit",
				"home_visit_leave": leave,
				"status": status,
			}
		).insert(ignore_permissions=True)
	finally:
		frappe.flags.hostel360_migration = False
	frappe.db.set_value("Home Visit Leave", leave, "mess_reduction", doc.name, update_modified=False)
	return doc.name


def run_form_action(doc, method: str, **args):
	"""Call a form button's controller method the way the desk does: on the copy the form sends."""
	from frappe.handler import run_doc_method

	with patch.object(frappe.local, "request", SimpleNamespace(method="POST"), create=True):
		run_doc_method(method, docs=frappe.as_json(doc.as_dict()), args=frappe.as_json(args))


def set_hostel_settings(**values) -> None:
	for fieldname, value in values.items():
		frappe.db.set_single_value("Hostel Settings", fieldname, value)
	frappe.clear_document_cache("Hostel Settings", "Hostel Settings")


def leave_data(start_in_hours: float = 24, hours: float = 72, **extra) -> dict:
	start = add_to_date(now_datetime(), hours=start_in_hours)
	end = add_to_date(start, hours=hours)
	return {
		"from_datetime": start,
		"to_datetime": end,
		"destination": "Madurai",
		"reason": "Family function",
		"contact_during_leave": unique_mobile(),
		**extra,
	}


class SentMessages(list):
	def events(self) -> list[str]:
		return [m["event"] for m in self]

	def recipients(self, event: str) -> set[str]:
		users = set()
		for message in self:
			if message["event"] == event:
				users.update(r.get("user") for r in message["recipients"] if r.get("user"))
		return users

	def references(self, event: str | None = None) -> list[str]:
		return [m["reference_name"] for m in self if event is None or m["event"] == event]

	def contexts(self, event: str, user: str | None = None) -> list[dict]:
		"""Contexts sent for ``event``, only those addressed to ``user`` when one is given."""
		return [
			m["context"]
			for m in self
			if m["event"] == event and (user is None or any(r.get("user") == user for r in m["recipients"]))
		]


class _RecordingDispatch:
	"""Stands in for hostel_communication.dispatch: records what would be queued.

	Only ``notify`` is provided, so recipients are resolved by the module's own fallbacks.
	"""

	def __init__(self, sent: SentMessages):
		self.sent = sent

	def notify(self, event, recipients, context, reference_doctype=None, reference_name=None, channels=None):
		self.sent.append(
			{
				"event": event,
				"recipients": recipients,
				"context": context,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
			}
		)


@contextmanager
def capture_notifications():
	"""Collect Student Services notifications instead of queueing them."""
	sent = SentMessages()
	with patch("hostel360.student_services.notifications._dispatch", return_value=_RecordingDispatch(sent)):
		yield sent
