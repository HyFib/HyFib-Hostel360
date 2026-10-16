"""Public page /demo: what the demonstration site shows and how to sign in to it.

The page exists only on a demonstration site (``hostel360.demo``); anywhere else it is a
plain 404, so a production site never publishes sign-in details. The accounts, the shared
password and the portal code come from site_config.json, where ``h360-demo-site`` wrote
them, so running the command again (which rotates the password) updates the page.
"""

import frappe
from frappe import _

no_cache = 1
sitemap = 0

PORTAL_SIGN_IN = "/hostel/login"
DESK_SIGN_IN = "/login"


def get_context(context):
	from hostel360.demo import (
		banner_text,
		demo_otp,
		demo_password,
		is_demo_site,
		portal_accounts,
		staff_accounts,
	)

	if not is_demo_site():
		raise frappe.PageDoesNotExistError
	context.no_cache = 1
	context.title = _("Hostel360 demonstration")
	context.institution = get_institution()
	context.banner = banner_text()
	context.otp = demo_otp()
	context.password = demo_password()
	context.students = [row for row in portal_accounts() if row["kind"] == "student"]
	context.parents = [row for row in portal_accounts() if row["kind"] == "parent"]
	context.staff = staff_accounts()
	context.hostel_count = frappe.db.count("Hostel", {"is_active": 1})
	context.student_count = frappe.db.count("Hostel Student", {"status": "Active"})
	context.portal_sign_in = PORTAL_SIGN_IN
	context.desk_sign_in = DESK_SIGN_IN
	return context


def get_institution() -> dict:
	try:
		from hostel360.utils.jinja import get_institution_details

		return get_institution_details()
	except frappe.DoesNotExistError:
		return {}
