"""Hostel People has no seed data of its own (masters are installed by Hostel Setup).

On every migrate it re-derives data that other records already hold, so it heals itself
after manual database corrections or an interrupted data migration:

* room and hostel occupancy counters, from the beds;
* each student's ``last_hostel``, from their allotments.
"""

import frappe


def install_defaults() -> None:
	if frappe.db.table_exists("Hostel Bed") and frappe.db.table_exists("Hostel Room"):
		from hostel360.hostel_setup.occupancy import refresh_all

		refresh_all()
	backfill_last_hostel()


def backfill_last_hostel() -> int:
	"""Fill ``Hostel Student.last_hostel`` where it is missing or out of step. Idempotent.

	A resident's last hostel is the one they live in. A former resident's is the hostel of
	their most recent allotment that was not cancelled; a value already set for them is
	kept, because it was written when they last held a bed. Returns the number of students
	updated.
	"""
	if not frappe.db.table_exists("Hostel Student"):
		return 0
	if not frappe.db.has_column("Hostel Student", "last_hostel"):
		return 0

	student = frappe.qb.DocType("Hostel Student")
	residents = (
		frappe.qb.from_(student)
		.select(student.name, student.current_hostel)
		.where(student.current_hostel.isnotnull() & (student.current_hostel != ""))
		.where(student.last_hostel.isnull() | (student.last_hostel != student.current_hostel))
	).run(as_dict=True)
	for row in residents:
		frappe.db.set_value(
			"Hostel Student", row.name, "last_hostel", row.current_hostel, update_modified=False
		)

	former = []
	if frappe.db.table_exists("Room Allotment"):
		allotment = frappe.qb.DocType("Room Allotment")
		former = (
			frappe.qb.from_(student)
			.select(student.name)
			.where(student.current_hostel.isnull() | (student.current_hostel == ""))
			.where(student.last_hostel.isnull() | (student.last_hostel == ""))
			.where(
				student.name.isin(
					frappe.qb.from_(allotment)
					.select(allotment.student)
					.where(allotment.docstatus == 1)
					.where(allotment.status != "Cancelled")
				)
			)
		).run(pluck=True)

	updated = len(residents)
	if former:
		from hostel360.hostel_people.doctype.room_allotment.room_allotment import last_allotted_hostel

		for name in former:
			hostel = last_allotted_hostel(name)
			if hostel:
				frappe.db.set_value("Hostel Student", name, "last_hostel", hostel, update_modified=False)
				updated += 1
	return updated
