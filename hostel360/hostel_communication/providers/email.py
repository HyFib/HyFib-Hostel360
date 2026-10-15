"""E-mail through Frappe's outgoing Email Account.

Notifications go into the Email Queue (delayed sending), which retries SMTP failures on its
own; a message counts as sent once it is queued. Sign-in codes are the exception: they go
straight to the mail server inside the sign-in request (``send_email_now``), so the code is
never stored and the send is bounded like an SMS. Portal users without an e-mail address
have placeholder logins on the ``.invalid`` TLD, which are never written to.
"""

import frappe
from frappe.utils import cint, validate_email_address

from hostel360.hostel_communication.providers.base import (
	DEFAULT_TIMEOUT,
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


# ---------------------------------------------------------------- sign-in codes

DIRECT_PROVIDER = "SMTP"
# Placeholders Frappe's queued sender fills in for each recipient; a direct send has none
# of what they stand for (open tracking, unsubscribe links, copies).
QUEUE_PLACEHOLDERS = ("<!--email_open_check-->", "<!--unsubscribe_url-->", "<!--cc_message-->")


def send_email_now(
	to: str,
	subject: str,
	body: str,
	*,
	timeout: float | tuple[float, float] | None = None,
	settings=None,
) -> DeliveryResult:
	"""Hand one message straight to the outgoing mail server, inside the current request.

	This is the path for sign-in codes. ``frappe.sendmail`` would keep the message, code and
	all, in the Email Queue, and its sender gives the mail server two minutes per step; here
	nothing is stored and each step of the SMTP conversation gets ``timeout`` seconds (the
	larger value of a (connect, read) pair). The default outgoing Email Account must send by
	SMTP.
	"""
	settings = settings or get_settings()
	if not cint(settings.get("email_enabled")):
		return DeliveryResult.skip(DIRECT_PROVIDER, "E-mail is not enabled in Messaging Settings.")
	if not to:
		return DeliveryResult.skip(DIRECT_PROVIDER, "No e-mail address.")
	if is_placeholder_address(to):
		return DeliveryResult.skip(
			DIRECT_PROVIDER, "Placeholder address; e-mail is not sent to .invalid domains."
		)
	if not validate_email_address(to):
		return DeliveryResult.failed(DIRECT_PROVIDER, "The e-mail address is not valid.")
	if is_test_mode(settings):
		return test_mode_result(DIRECT_PROVIDER)
	if frappe.are_emails_muted():
		return DeliveryResult.skip(DIRECT_PROVIDER, "Outgoing e-mail is muted on this site.")

	# Frappe reports mail server trouble as on-screen messages for a desk user. In a sign-in
	# request they would reach a caller who must not learn anything about the number.
	messages = list(getattr(frappe.local, "message_log", None) or [])
	try:
		account = outgoing_smtp_account()
		if not account:
			return DeliveryResult.failed(
				DIRECT_PROVIDER, "No default outgoing Email Account that sends by SMTP is set up."
			)
		sender, message = build_message(account, to, subject, body)
		_transmit(account, sender, to, message, _seconds(timeout))
	except Exception as e:
		return request_failure(DIRECT_PROVIDER, e)
	finally:
		frappe.local.message_log = messages
	return DeliveryResult.sent(DIRECT_PROVIDER)


def outgoing_smtp_account():
	"""The default outgoing Email Account when it sends by SMTP, else None."""
	from frappe.email.doctype.email_account.email_account import EmailAccount

	account = EmailAccount.find_outgoing()
	if not account or account.get("service") == "Frappe Mail" or not account.get("smtp_server"):
		return None
	return account


def build_message(account, to: str, subject: str, body: str) -> tuple[str, str]:
	"""Sender and MIME text of a message, laid out like Frappe's own e-mails."""
	from frappe.email.email_body import get_email

	mail = get_email(
		recipients=[to],
		subject=subject or "",
		content=body or "",
		email_account=account,
		expose_recipients="header",
	)
	# Mail without a Message-Id looks like spam to many filters.
	mail.set_message_id(None, is_notification=True)
	message = mail.as_string()
	for placeholder in QUEUE_PLACEHOLDERS:
		message = message.replace(placeholder, "")
	return mail.sender, message.replace("<!--recipient-->", to)


def _transmit(account, sender: str, to: str, message: str, timeout: float) -> None:
	from frappe.email.smtp import SMTPServer

	if frappe.in_test and not frappe.flags.testing_email:
		# As in Frappe's own queued sender: test runs never talk to a mail server.
		return
	server = SMTPServer(**{**account.sendmail_config(), "timeout": timeout})
	try:
		server.session.sendmail(from_addr=sender, to_addrs=[to], msg=message.encode("utf-8"))
	finally:
		try:
			server.quit()
		except Exception:
			# Closing is best effort: it must not turn a delivered message into a failure, and
			# a connection that will not close cleanly times out on its own.
			frappe.logger("hostel360").warning("Could not close the SMTP connection", exc_info=True)


def _seconds(timeout) -> float:
	if isinstance(timeout, tuple | list):
		return float(max(timeout))
	return float(timeout or DEFAULT_TIMEOUT)
