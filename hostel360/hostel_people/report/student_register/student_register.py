# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from hostel360.utils.validation import mask_mobile

FILTER_FIELDS = (
	("hostel", "current_hostel"),
	("program", "program"),
	("department", "department"),
	("year_of_study", "year_of_study"),
	("gender", "gender"),
	("status", "status"),
	("validation_status", "validation_status"),
	("academic_year", "academic_year"),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	students = get_students(filters)
	# Read-only viewers (auditors, mess managers) see masked numbers: data minimisation.
	full_numbers = bool(frappe.has_permission("Hostel Student", "write"))
	guardians = get_primary_guardians([s.name for s in students])

	data = []
	for student in students:
		guardian = guardians.get(student.name) or {}
		data.append(
			{
				**student,
				"mobile_number": student.mobile_number if full_numbers else mask_mobile(student.mobile_number),
				"guardian_name": guardian.get("guardian_name"),
				"guardian_relation": guardian.get("relation"),
				"guardian_mobile": guardian.get("mobile_number")
				if full_numbers
				else mask_mobile(guardian.get("mobile_number")),
				"portal_user": 1 if student.user else 0,
			}
		)
	return get_columns(), data, None, None, get_summary(students)


def get_students(filters) -> list[dict]:
	query_filters = {}
	for key, fieldname in FILTER_FIELDS:
		if filters.get(key):
			query_filters[fieldname] = filters.get(key)
	if filters.get("allotted") == "Allotted":
		query_filters["current_allotment"] = ["is", "set"]
	elif filters.get("allotted") == "Not Allotted":
		query_filters["current_allotment"] = ["is", "not set"]

	return frappe.get_list(
		"Hostel Student",
		filters=query_filters,
		fields=[
			"name",
			"full_name",
			"roll_number",
			"gender",
			"program",
			"department",
			"year_of_study",
			"mobile_number",
			"email",
			"current_hostel",
			"current_room",
			"current_bed",
			"status",
			"validation_status",
			"profile_confirmed",
			"privacy_consent_version",
			"mess_enrolled",
			"user",
		],
		order_by="current_hostel asc, current_room asc, full_name asc",
		limit_page_length=0,
	)


def get_primary_guardians(students: list[str]) -> dict:
	result = {}
	for start in range(0, len(students), 500):
		chunk = students[start : start + 500]
		for row in frappe.get_all(
			"Hostel Student Guardian",
			filters={"parenttype": "Hostel Student", "parent": ["in", chunk], "is_primary": 1},
			fields=["parent", "guardian_name", "relation", "mobile_number"],
		):
			result[row.parent] = row
	return result


def get_columns() -> list[dict]:
	return [
		{"label": _("Register No."), "fieldname": "name", "fieldtype": "Link", "options": "Hostel Student", "width": 130},
		{"label": _("Name"), "fieldname": "full_name", "fieldtype": "Data", "width": 180},
		{"label": _("Roll No."), "fieldname": "roll_number", "fieldtype": "Data", "width": 100},
		{"label": _("Gender"), "fieldname": "gender", "fieldtype": "Data", "width": 80},
		{"label": _("Program"), "fieldname": "program", "fieldtype": "Link", "options": "Academic Program", "width": 180},
		{"label": _("Year"), "fieldname": "year_of_study", "fieldtype": "Data", "width": 60},
		{"label": _("Mobile"), "fieldname": "mobile_number", "fieldtype": "Data", "width": 120},
		{"label": _("Hostel"), "fieldname": "current_hostel", "fieldtype": "Link", "options": "Hostel", "width": 80},
		{"label": _("Room"), "fieldname": "current_room", "fieldtype": "Link", "options": "Hostel Room", "width": 100},
		{"label": _("Bed"), "fieldname": "current_bed", "fieldtype": "Link", "options": "Hostel Bed", "width": 110},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Validation"), "fieldname": "validation_status", "fieldtype": "Data", "width": 100},
		{"label": _("Guardian"), "fieldname": "guardian_name", "fieldtype": "Data", "width": 160},
		{"label": _("Relation"), "fieldname": "guardian_relation", "fieldtype": "Data", "width": 90},
		{"label": _("Guardian Mobile"), "fieldname": "guardian_mobile", "fieldtype": "Data", "width": 120},
		{"label": _("Mess"), "fieldname": "mess_enrolled", "fieldtype": "Check", "width": 60},
		{"label": _("Profile Confirmed"), "fieldname": "profile_confirmed", "fieldtype": "Check", "width": 90},
		{"label": _("Consent Version"), "fieldname": "privacy_consent_version", "fieldtype": "Data", "width": 90},
		{"label": _("Portal User"), "fieldname": "portal_user", "fieldtype": "Check", "width": 80},
		{"label": _("Department"), "fieldname": "department", "fieldtype": "Link", "options": "Academic Department", "width": 160},
	]


def get_summary(students: list[dict]) -> list[dict]:
	total = len(students)
	allotted = sum(1 for s in students if s.current_bed)
	return [
		{"value": total, "label": _("Students"), "datatype": "Int", "indicator": "Blue"},
		{"value": allotted, "label": _("Allotted"), "datatype": "Int", "indicator": "Green"},
		{"value": total - allotted, "label": _("Without Room"), "datatype": "Int", "indicator": "Orange"},
		{
			"value": sum(1 for s in students if s.validation_status == "Pending"),
			"label": _("Pending Validation"),
			"datatype": "Int",
			"indicator": "Orange",
		},
		{
			"value": sum(1 for s in students if s.profile_confirmed),
			"label": _("Profile Confirmed"),
			"datatype": "Int",
			"indicator": "Green",
		},
	]
