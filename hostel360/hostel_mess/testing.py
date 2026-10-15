"""Fixture builders for the Hostel Mess test suites.

Records owned by other modules (hostels, students, room allotments) are written straight
to the database with ``db_insert`` so these tests exercise mess rules only and do not
depend on how the other modules validate their own documents.
"""

import random

import frappe
from frappe.utils import getdate, nowdate

# Linked DocTypes whose test records belong to other modules.
EXTERNAL_DEPENDENCIES = [
	"Hostel",
	"Hostel Student",
	"Room Allotment",
	"Hostel Fee Invoice",
	"Home Visit Leave",
	"Hostel Settings",
]


def unique(prefix: str) -> str:
	return f"{prefix}-{frappe.generate_hash(length=10).upper()}"


def _db_insert(doctype: str, name: str, values: dict, docstatus: int = 0):
	doc = frappe.new_doc(doctype)
	doc.update(values)
	doc.name = name
	doc.docstatus = docstatus
	doc.db_insert()
	return doc


def make_hostel(code: str | None = None, gender: str = "Male") -> str:
	code = code or unique("TH")
	_db_insert(
		"Hostel",
		code,
		{"hostel_code": code, "hostel_name": f"Test Hostel {code}", "gender": gender, "is_active": 1},
	)
	return code


def make_student(
	hostel: str | None = None,
	mess_enrolled: int = 1,
	status: str = "Active",
	user: str | None = None,
	first_name: str | None = None,
) -> str:
	register_number = unique("TS")
	first_name = first_name or f"Student {register_number[-10:]}"
	_db_insert(
		"Hostel Student",
		register_number,
		{
			"register_number": register_number,
			"first_name": first_name,
			"full_name": first_name,
			"gender": "Male",
			"mobile_number": f"9{random.randint(100000000, 999999999)}",
			"status": status,
			"validation_status": "Validated",
			"mess_enrolled": mess_enrolled,
			"current_hostel": hostel,
			"user": user,
		},
	)
	return register_number


def make_allotment(
	student: str,
	hostel: str,
	from_date,
	to_date=None,
	vacated_on=None,
	status: str = "Active",
	previous_allotment: str | None = None,
	docstatus: int = 1,
) -> str:
	name = unique("TALT")
	_db_insert(
		"Room Allotment",
		name,
		{
			"student": student,
			"hostel": hostel,
			"bed": f"{hostel}-BED",
			"allotment_type": "Transfer" if previous_allotment else "New",
			"previous_allotment": previous_allotment,
			"from_date": getdate(from_date),
			"to_date": getdate(to_date) if to_date else None,
			"vacated_on": getdate(vacated_on) if vacated_on else None,
			"status": status,
		},
		docstatus=docstatus,
	)
	return name


def make_user(roles: list[str], user_type: str = "System User") -> str:
	email = f"{unique('mess').lower()}@example.com"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"user_type": user_type,
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.ignore_permissions = True
	user.flags.no_welcome_mail = True
	user.insert()
	return email


def make_mess(hostels: list[str], manager: str | None = None) -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "Hostel Mess",
				"mess_name": unique("Test Mess"),
				"mess_manager": manager,
				"is_active": 1,
				"hostels": [{"hostel": h} for h in hostels],
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_item(uom: str = "Kg", reorder_level: float = 0, category: str = "Grains") -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "Mess Item",
				"item_name": unique("Test Item"),
				"category": category,
				"uom": uom,
				"reorder_level": reorder_level,
				"is_active": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_stock_entry(
	mess: str,
	entry_type: str,
	rows: list[tuple],
	posting_date=None,
	posting_time: str = "10:00:00",
	submit: bool = True,
	**values,
):
	"""``rows`` are ``(item, qty)`` or ``(item, qty, rate)`` tuples."""
	doc = frappe.get_doc(
		{
			"doctype": "Mess Stock Entry",
			"entry_type": entry_type,
			"mess": mess,
			"posting_date": getdate(posting_date or nowdate()),
			"posting_time": posting_time,
			"items": [{"item": row[0], "qty": row[1], "rate": row[2] if len(row) > 2 else 0} for row in rows],
			**values,
		}
	)
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def make_expense(mess: str, amount: float, posting_date, expense_type: str = "Gas", submit: bool = True):
	doc = frappe.get_doc(
		{
			"doctype": "Mess Expense",
			"mess": mess,
			"posting_date": getdate(posting_date),
			"expense_type": expense_type,
			"amount": amount,
		}
	).insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def make_reduction(
	student: str, from_date, to_date, status: str = "Approved", reason: str = "Home Visit"
) -> str:
	"""A historical reduction with a given status (bypasses notice and approval flow)."""
	frappe.flags.hostel360_migration = True
	try:
		doc = frappe.get_doc(
			{
				"doctype": "Mess Reduction Application",
				"student": student,
				"from_date": getdate(from_date),
				"to_date": getdate(to_date),
				"reason": reason,
				"status": status,
			}
		).insert(ignore_permissions=True)
	finally:
		frappe.flags.hostel360_migration = False
	return doc.name


def set_mess_settings(**values) -> None:
	for fieldname, value in values.items():
		frappe.db.set_single_value("Mess Settings", fieldname, value)


# Transactions keyed by mess, with their child tables.
_MESS_RECORDS = (
	("Mess Stock Ledger Entry", None),
	("Mess Stock Entry", "Mess Stock Entry Item"),
	("Mess Expense", None),
	("Mess Extra Charge", None),
	("Mess Bill", None),
	("Mess Bill Run", None),
	("Mess Reduction Application", None),
	("Mess Indent", "Mess Indent Item"),
	("Mess Menu", "Mess Menu Item"),
)


def purge_committed_records(
	messes=(), items=(), hostels=(), students=(), users=(), extra: dict | None = None
) -> None:
	"""Remove what a multi-connection test had to commit, and commit the removal.

	Tests that simulate two users need their fixtures committed so the second connection
	can see them; the usual rollback at the end of a test does not undo that.
	"""
	names = []
	for mess in messes:
		for doctype, child in _MESS_RECORDS:
			rows = frappe.get_all(doctype, filters={"mess": mess}, pluck="name")
			names += rows
			if child and rows:
				frappe.db.delete(child, {"parent": ["in", rows], "parenttype": doctype})
			frappe.db.delete(doctype, {"mess": mess})
		frappe.db.delete("Hostel Mess Hostel", {"parent": mess, "parenttype": "Hostel Mess"})
		frappe.db.delete("Hostel Mess", {"name": mess})
		names.append(mess)
	for student in students:
		names += frappe.get_all("Room Allotment", filters={"student": student}, pluck="name")
		frappe.db.delete("Room Allotment", {"student": student})
		frappe.db.delete("Hostel Student", {"name": student})
		names.append(student)
	for doctype, values in (("Mess Item", items), ("Hostel", hostels), ("User", users)):
		for name in values:
			frappe.db.delete(doctype, {"name": name})
			names.append(name)
	for user in users:
		frappe.db.delete("Has Role", {"parent": user, "parenttype": "User"})
		frappe.db.delete("User Permission", {"user": user})
	for doctype, values in (extra or {}).items():
		for name in values:
			frappe.db.delete(doctype, {"name": name})
			names.append(name)
	if names:
		frappe.db.delete("Version", {"docname": ["in", names]})
	frappe.db.commit()
