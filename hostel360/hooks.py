app_name = "hostel360"
app_title = "HyFib Hostel360"
app_publisher = "HyFib Solutions Private Limited"
app_description = "Cloud-based hostel management software"
app_email = "support@hyfib.com"
app_license = "mit"
app_logo_url = "/assets/hostel360/images/hostel360-logo.svg"

# Apps
# ------------------

add_to_apps_screen = [
	{
		"name": "hostel360",
		"logo": "/assets/hostel360/images/hostel360-logo.svg",
		"title": "Hostel360",
		"route": "/desk/hostel-admin",
		"has_permission": "hostel360.setup.permissions.has_app_permission",
	}
]

# Includes in <head>
# ------------------

app_include_js = ["hostel360_desk.bundle.js"]
app_include_css = ["hostel360_desk.bundle.css"]

# Website
# ------------------

# Student and parent portal is a single-page app served from /hostel
website_route_rules = [
	{"from_route": "/hostel/<path:app_path>", "to_route": "hostel"},
]

website_redirects = [
	{"source": "/portal", "target": "/hostel"},
	{"source": "/student", "target": "/hostel"},
	{"source": "/parent", "target": "/hostel"},
]

role_home_page = {
	"Hostel Student": "hostel",
	"Hostel Parent": "hostel",
}

get_website_user_home_page = "hostel360.auth.events.get_website_user_home_page"

# Jinja
# ----------

jinja = {
	"methods": [
		"hostel360.utils.jinja.format_inr",
		"hostel360.utils.jinja.amount_in_words_inr",
		"hostel360.utils.jinja.get_institution_details",
	],
}

# Installation
# ------------

after_install = "hostel360.setup.install.after_install"
after_migrate = "hostel360.setup.install.after_migrate"
before_tests = "hostel360.setup.install.before_tests"

# Session and authentication
# --------------------------

on_login = "hostel360.auth.events.on_login"
on_session_creation = "hostel360.auth.events.on_session_creation"

extend_bootinfo = "hostel360.setup.boot.extend_bootinfo"

# Permissions
# -----------
# Deputy wardens and mess staff are scoped to their hostels through User Permissions
# that are synchronised from Hostel Staff assignments. Portal users (students and
# parents) never get DocType permissions; they only use the whitelisted portal API.

permission_query_conditions = {
	"Hostel Grievance": "hostel360.student_services.permissions.grievance_query_conditions",
}

has_permission = {
	"Hostel Grievance": "hostel360.student_services.permissions.grievance_has_permission",
}

# Document Events
# ---------------

doc_events = {
	"File": {
		"before_insert": "hostel360.security.files.validate_upload",
		"after_insert": "hostel360.security.files.enqueue_malware_scan",
	},
	"User": {
		"validate": "hostel360.auth.events.validate_user",
	},
	# A HyFib reply on a Support Ticket counts as the first response straight away
	"Comment": {
		"after_insert": "hostel360.hostel_operations.support.on_comment",
	},
}

# Scheduled Tasks
# ---------------
# Site time zone is Asia/Kolkata; cron expressions are evaluated in that zone.

scheduler_events = {
	"cron": {
		# HyFib operations: alert escalation follows the escalation matrix
		"* * * * *": [
			"hostel360.hostel_operations.escalation.run_escalations",
		],
		"*/5 * * * *": [
			"hostel360.hostel_operations.health.run_health_checks",
			"hostel360.hostel_operations.support.check_ticket_slas",
		],
		"*/15 * * * *": [
			"hostel360.hostel_fees.reconciliation.reconcile_pending_transactions",
			"hostel360.hostel_communication.dispatch.retry_failed_messages",
		],
		# 08:00 IST: fee reminders for dues falling due soon or overdue
		"0 8 * * *": [
			"hostel360.hostel_fees.reminders.send_fee_reminders",
		],
		# 23:30 IST: daily gateway settlement reconciliation
		"30 23 * * *": [
			"hostel360.hostel_fees.reconciliation.daily_reconciliation",
		],
		# 07:00 IST on the 1st: monthly server performance report for the previous month
		"0 7 1 * *": [
			"hostel360.hostel_operations.reports.generate_monthly_performance_report",
		],
	},
	"hourly": [
		"hostel360.student_services.grievance_tasks.escalate_overdue_grievances",
		"hostel360.student_services.leave_tasks.mark_overdue_returns",
	],
	"daily": [
		"hostel360.hostel_fees.tasks.mark_overdue_invoices",
		"hostel360.hostel_people.tasks.close_ended_allotments",
		"hostel360.hostel_operations.maintenance.purge_old_metric_samples",
		"hostel360.api.portal.delete_stale_uploads",
	],
	"weekly": [
		"hostel360.hostel_mess.tasks.send_low_stock_digest",
	],
}

# Testing
# -------

# Ignore links to specified DocTypes when deleting documents
ignore_links_on_delete = ["Message Log", "Ops Alert", "Ops Metric Sample"]

# Log retention (days); CERT-In directions require at least 180 days of logs
default_log_clearing_doctypes = {
	"Message Log": 365,
	"Ops Metric Sample": 400,
}

# User Data Protection (DPDP Act 2023: personal data export / erasure requests)
# --------------------

user_data_fields = [
	{
		"doctype": "Hostel Student",
		"filter_by": "user",
		"redact_fields": ["mobile_number", "email", "date_of_birth", "address_line1", "address_line2"],
		"partial": 1,
	},
	{
		"doctype": "Hostel Guardian",
		"filter_by": "user",
		"redact_fields": ["mobile_number", "email", "address"],
		"partial": 1,
	},
]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True
