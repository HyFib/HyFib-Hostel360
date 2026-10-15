"""E-mail through Frappe's outgoing Email Account.

Messages go into the Email Queue (delayed sending), which retries SMTP failures on its
own; a message counts as sent once it is queued. Portal users without an e-mail address
have placeholder logins on the ``.invalid`` TLD, which are never written to.
"""

import frappe
from frappe.utils import cint, validate_email_address

from hostel360.hostel_communication.providers.base import (
	DeliveryResult,
	get_settings,
	is_test_mode,
	request_failure,
	test_mode_result,
)

PROVIDER = "Email Queue"


def is_placeholder_address(email: str | None) -> bool:
	return bool(email) and email.strip().lower().rstrip(".").endswith(".invalid")


def send_email(
	to: str,
	subject: str,
	body: str,
	*,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	settings=None,
) -> DeliveryResult:
	settings = settings or get_settings()
	if not cint(settings.get("email_enabled")):
		return DeliveryResult.skip(PROVIDER, "E-mail is not enabled in Messaging Settings.")
	if not to:
		return DeliveryResult.skip(PROVIDER, "No e-mail address.")
	if is_placeholder_address(to):
		return DeliveryResult.skip(PROVIDER, "Placeholder address; e-mail is not sent to .invalid domains.")
	if not validate_email_address(to):
		return DeliveryResult.failed(PROVIDER, "The e-mail address is not valid.")
	if is_test_mode(settings):
		return test_mode_result(PROVIDER)

	try:
		queue = frappe.sendmail(
			recipients=[to],
			subject=subject or "",
			message=body or "",
			delayed=True,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			add_unsubscribe_link=0,
		)
	except Exception as e:
		return request_failure(PROVIDER, e)
	return DeliveryResult.sent(PROVIDER, getattr(queue, "name", None))
