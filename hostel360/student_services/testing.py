"""Fixtures for Student Services tests.

Every helper creates fresh records with unique values so tests never depend on each
other or on data from other modules' tests.
"""

import random
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

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


def make_guardian(student: str, can_approve_leave: int = 1, relation: str = "Father", with_user: bool = True) -> frappe._dict:
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
