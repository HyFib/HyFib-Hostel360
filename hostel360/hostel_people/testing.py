"""Record factories for Hostel Setup and Hostel People tests.

Every factory uses fresh, random codes so tests never depend on each other's data or on
records another module's tests created.
"""

import random

import frappe
from frappe.utils import nowdate


def unique_code(prefix: str = "T", length: int = 5) -> str:
	return (prefix + frappe.generate_hash(length=length)).upper()


def random_mobile() -> str:
	while True:
		mobile = str(random.choice("6789")) + "".join(random.choice("0123456789") for _ in range(9))
		if not frappe.db.exists("Hostel Student", {"mobile_number": mobile}) and not frappe.db.exists(
			"Hostel Guardian", {"mobile_number": mobile}
		):
			return mobile


def make_hostel(gender: str = "Male", **values):
	code = values.pop("hostel_code", None) or unique_code("T")
	return frappe.get_doc(
		{
			"doctype": "Hostel",
			"hostel_name": f"Test Hostel {code}",
			"hostel_code": code,
			"gender": gender,
			"is_active": 1,
			**values,
		}
	).insert(ignore_permissions=True)


def make_block(hostel: str, block_code: str = "A"):
	return frappe.get_doc(
		{
			"doctype": "Hostel Block",
			"hostel": hostel,
			"block_name": f"Block {block_code}",
			"block_code": block_code,
		}
	).insert(ignore_permissions=True)


def make_floor(block: str, floor_number: int = 1):
	return frappe.get_doc({"doctype": "Hostel Floor", "block": block, "floor_number": floor_number}).insert(
		ignore_permissions=True
	)


ROOM_TYPE_FOR_CAPACITY = {1: "Single", 2: "Double", 3: "Triple", 4: "Four Sharing"}


def make_room(hostel: str, capacity: int = 2, room_number: str | None = None, **values):
	return frappe.get_doc(
		{
			"doctype": "Hostel Room",
			"hostel": hostel,
			"room_number": room_number or unique_code("R", 4),
			"room_type": values.pop("room_type", None) or ROOM_TYPE_FOR_CAPACITY.get(capacity, "Dormitory"),
			"capacity": capacity,
			**values,
		}
	).insert(ignore_permissions=True)


def beds_of(room: str) -> list[str]:
	"""Active bed names of a room in label order (A, B, C...)."""
	beds = frappe.get_all("Hostel Bed", filters={"room": room, "is_active": 1}, fields=["name", "bed_label"])
	return [b.name for b in sorted(beds, key=lambda b: (len(b.bed_label), b.bed_label))]


def make_student(gender: str = "Male", **values):
	register_number = values.pop("register_number", None) or unique_code("TS", 6)
	return frappe.get_doc(
		{
			"doctype": "Hostel Student",
			"register_number": register_number,
			"first_name": values.pop("first_name", "Test"),
			"last_name": values.pop("last_name", f"Student {register_number[-4:]}"),
			"gender": gender,
			"mobile_number": values.pop("mobile_number", None) or random_mobile(),
			"status": values.pop("status", "Active"),
			**values,
		}
	).insert(ignore_permissions=True)


def make_guardian(**values):
	return frappe.get_doc(
		{
			"doctype": "Hostel Guardian",
			"guardian_name": values.pop("guardian_name", "Test Guardian"),
			"relation": values.pop("relation", "Father"),
			"mobile_number": values.pop("mobile_number", None) or random_mobile(),
			**values,
		}
	).insert(ignore_permissions=True)


def allot(student: str, bed: str, from_date: str | None = None, **values):
	doc = frappe.get_doc(
		{
			"doctype": "Room Allotment",
			"student": student,
			"bed": bed,
			"allotment_type": values.pop("allotment_type", "New"),
			"from_date": from_date or nowdate(),
			**values,
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def make_staff(designation: str, assignments=(), **values):
	"""A Hostel Staff record, with a desk login unless ``create_user=0`` is passed.

	``assignments`` holds hostel names, or ``{"hostel": ..., "block": ...}`` rows for block
	assignments. The record is inserted as the current user, so permissions apply.
	"""
	suffix = frappe.generate_hash(length=8)
	rows = [row if isinstance(row, dict) else {"hostel": row} for row in assignments]
	return frappe.get_doc(
		{
			"doctype": "Hostel Staff",
			"staff_name": values.pop("staff_name", f"Test Staff {suffix}"),
			"designation": designation,
			"email": values.pop("email", f"staff-{suffix}@example.com"),
			"create_user": values.pop("create_user", 1),
			"send_welcome_email": 0,
			"is_active": values.pop("is_active", 1),
			"assignments": rows,
			**values,
		}
	).insert()


def make_desk_user(*roles: str, email: str | None = None) -> str:
	"""A plain desk account holding ``roles`` (not linked to any Hostel Staff record)."""
	email = email or f"desk-{frappe.generate_hash(length=8)}@example.com"
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Desk",
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	).insert(ignore_permissions=True)
	return email
