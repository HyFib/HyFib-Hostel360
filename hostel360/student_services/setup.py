"""Install-time defaults for Student Services.

Student Services has no seed data. Its DocTypes, the Bonafide Certificate print format, the
reports and the approvals page are synced from the app by migrate, and the message templates
for its events are installed by Hostel Communication. What runs here repairs records written
before a rule existed; every step is idempotent, so it is safe on every migrate.
"""

import json

import frappe
from frappe import _

from hostel360.student_services.doctype.hostel_feedback.hostel_feedback import ANONYMOUS_OWNER
from hostel360.student_services.doctype.hostel_grievance.hostel_grievance import (
	CLOSED_MESSAGE,
	ESCALATED_MESSAGE,
	REFERRED_MESSAGE,
	REGISTERED_MESSAGE,
)

# Records that carry the student's hostel for hostel-scoped access.
HOSTEL_SCOPED_DOCTYPES = (
	"Home Visit Leave",
	"Hostel Grievance",
	"Bonafide Certificate Request",
	"Hostel Feedback",
)


def install_defaults():
	forget_anonymous_submitters()
	fill_missing_hostels()
	tag_system_timeline_entries()


def forget_anonymous_submitters():
	"""Anonymous feedback once recorded the student's portal login as its creator."""
	frappe.db.sql(
		"""
		update `tabHostel Feedback`
		set modified_by = %(owner)s
		where is_anonymous = 1
			and modified_by != %(owner)s
			and (
				modified_by = owner
				or modified_by in (select name from `tabUser` where user_type = 'Website User')
			)
		""",
		{"owner": ANONYMOUS_OWNER},
	)
	frappe.db.sql(
		"""
		update `tabHostel Feedback`
		set owner = %(owner)s
		where is_anonymous = 1 and owner != %(owner)s
		""",
		{"owner": ANONYMOUS_OWNER},
	)


def fill_missing_hostels():
	"""Records of students without a room once had no hostel, which left them visible to the
	hostel-scoped staff of every hostel. They belong to the student's last hostel."""
	if not frappe.get_meta("Hostel Student").has_field("last_hostel"):
		return
	for doctype in HOSTEL_SCOPED_DOCTYPES:
		# The table name comes from the fixed tuple above; nothing here comes from input.
		frappe.db.sql(
			f"""
			update `tab{doctype}` r
			inner join `tabHostel Student` s on s.name = r.student
			set r.hostel = coalesce(nullif(s.last_hostel, ''), nullif(s.current_hostel, ''))
			where ifnull(r.hostel, '') = ''
				and coalesce(nullif(s.last_hostel, ''), nullif(s.current_hostel, '')) is not null
			"""  # noqa: S608
		)


def tag_system_timeline_entries():
	"""Grievance timeline entries the system wrote once took the language of whoever's request
	wrote them, and kept no template to translate them from. The fixed ones are put back into
	English and tagged, and so are escalations, so that every reader sees them in their own
	language. Entries naming an assignee are left as they are: their text cannot be told apart
	from a comment someone typed."""
	if not frappe.get_meta("Hostel Grievance Update").has_field("message_template"):
		return
	languages = frappe.get_all("Language", filters={"enabled": 1, "name": ["!=", "en"]}, pluck="name")
	for english in (REGISTERED_MESSAGE, REFERRED_MESSAGE, CLOSED_MESSAGE):
		variants = tuple(sorted({english, *(_(english, lang=lang) for lang in languages)}))
		frappe.db.sql(
			"""
			update `tabHostel Grievance Update`
			set message = %(english)s, message_template = %(english)s
			where ifnull(message_template, '') = '' and message in %(variants)s
			""",
			{"english": english, "variants": variants},
		)

	prefix = ESCALATED_MESSAGE.split("{0}")[0]
	rows = frappe.db.sql(
		"""
		select name, message from `tabHostel Grievance Update`
		where ifnull(message_template, '') = '' and update_by = 'Administrator' and message like %(pattern)s
		""",
		{"pattern": prefix.replace("%", "\\%").replace("_", "\\_") + "%"},
		as_dict=True,
	)
	for row in rows:
		level = row.message[len(prefix) :].rstrip(".")
		if level.isdigit():
			frappe.db.set_value(
				"Hostel Grievance Update",
				row.name,
				{"message_template": ESCALATED_MESSAGE, "message_args": json.dumps([level])},
				update_modified=False,
			)
