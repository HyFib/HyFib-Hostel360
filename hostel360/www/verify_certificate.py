"""Public page /verify-certificate: confirms a bonafide certificate from its QR code."""

import frappe
from frappe import _

from hostel360.student_services.verification import is_rate_limited, lookup_certificate, normalise_code

no_cache = 1
sitemap = 0


def get_context(context):
	context.no_cache = 1
	context.title = _("Verify Certificate")
	context.institution = get_institution()
	context.code = normalise_code(frappe.form_dict.get("code"))[:64]
	context.result = None
	context.rate_limited = False

	if context.code:
		if is_rate_limited():
			context.rate_limited = True
			context.http_status_code = 429
		else:
			context.result = lookup_certificate(context.code)
	return context


def get_institution() -> dict:
	try:
		from hostel360.utils.jinja import get_institution_details

		return get_institution_details()
	except frappe.DoesNotExistError:
		return {}
