"""Install-time defaults for Student Services.

Student Services has no seed data. Its DocTypes, the Bonafide Certificate print format, the
reports and the approvals page are synced from the app by migrate, and the message templates
for its events are installed by Hostel Communication. What runs here repairs records written
before a rule existed; every step is idempotent, so it is safe on every migrate.
"""

import frappe

from hostel360.student_services.doctype.hostel_feedback.hostel_feedback import ANONYMOUS_OWNER

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
