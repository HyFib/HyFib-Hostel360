"""Event notifications for students, parents and staff.

``notify`` is the single entry point used by every module. It queues a ``deliver`` job on
the short queue (after the caller's transaction commits) which renders the event's
Message Templates for each recipient, sends them through the enabled channels, writes a
Message Log row per message and creates a Notification Log for recipients with a user
account. Failed messages that may succeed later are retried by
``retry_failed_messages`` with exponential back-off.

Nothing in this module raises into the caller for delivery or template problems: a
broken template or an unreachable gateway must never block a fee payment or a leave
approval. Problems are recorded on the Message Log row and in the Error Log.
"""

import html
import json
import math

import frappe
from frappe.utils import add_to_date, cint, cstr, now_datetime, strip_html, validate_email_address
from frappe.utils.password import decrypt, encrypt

from hostel360.hostel_communication import events as ev
from hostel360.hostel_communication import rendering
from hostel360.hostel_communication.default_templates import get_default_template
from hostel360.hostel_communication.providers import email as email_provider
from hostel360.hostel_communication.providers import sms as sms_provider
from hostel360.hostel_communication.providers import whatsapp as whatsapp_provider
from hostel360.hostel_communication.providers.base import DeliveryResult, get_settings, record_outcome
from hostel360.setup.roles import ADMIN_ROLE, WARDEN_ROLE
from hostel360.utils.validation import mask_email, mask_mobile, normalize_mobile

DELIVER_METHOD = "hostel360.hostel_communication.dispatch.deliver"
NOTIFICATION_PROVIDER = "Notification Log"

MAX_ATTEMPTS = 3
RETRY_BASE_MINUTES = 15
RETRY_WINDOW_HOURS = 48
RETRY_BATCH_SIZE = 200
RECIPIENTS_PER_JOB = 25
MESSAGE_TEXT_LIMIT = 4000
SUBJECT_LIMIT = 140

# notify() stays silent while data is being installed, migrated or imported.
SUPPRESS_FLAGS = ("in_install", "in_migrate", "in_import", "hostel360_migration")

# Where a portal user lands when they tap an in-app notification.
PORTAL_LINKS = {
	ev.STUDENT_VALIDATED: "/hostel",
	ev.ROOM_ALLOTTED: "/hostel/profile",
	ev.FEE_DUE_PUBLISHED: "/hostel/fees",
	ev.FEE_REMINDER: "/hostel/fees",
	ev.PAYMENT_RECEIVED: "/hostel/receipts",
	ev.MESS_BILL_PUBLISHED: "/hostel/mess-bills",
	ev.MENU_PUBLISHED: "/hostel/menu",
	ev.MESS_REDUCTION_SUBMITTED: "/hostel/mess-reduction",
	ev.MESS_REDUCTION_DECIDED: "/hostel/mess-reduction",
	ev.LEAVE_SUBMITTED: "/hostel/leave",
	ev.LEAVE_PARENT_APPROVAL_REQUIRED: "/hostel/leave/approvals",
	ev.LEAVE_DECIDED: "/hostel/leave",
	ev.LEAVE_OVERDUE: "/hostel/leave",
	ev.GRIEVANCE_RECEIVED: "/hostel/grievances",
	ev.GRIEVANCE_UPDATED: "/hostel/grievances",
	ev.GRIEVANCE_RESOLVED: "/hostel/grievances",
	ev.BONAFIDE_DECIDED: "/hostel/certificates",
}

TEMPLATE_FIELDS = (
	"name",
	"channel",
	"subject",
	"body",
	"dlt_template_id",
	"msg91_template_id",
	"whatsapp_template_name",
	"whatsapp_language",
	"template_parameters",
)


# ---------------------------------------------------------------- public API


def is_suppressed() -> bool:
	return any(frappe.flags.get(flag) for flag in SUPPRESS_FLAGS)


def notify(
	event: str,
	recipients: list[dict],
	context: dict,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	channels: list[str] | None = None,
) -> None:
	"""Queue ``event`` for delivery to ``recipients``.

	``recipients`` are dicts with any of ``user``, ``mobile``, ``email``, ``name`` and
	``kind`` ("student" | "parent" | "staff"); use the ``recipients_for_*`` helpers to
	build them. ``context`` carries the variables listed for the event in
	``events.EVENT_CONTEXT``. ``channels`` restricts delivery to some channels, for
	example ``["In-App"]``.
	"""
	if is_suppressed():
		return
	try:
		if not ev.is_valid_event(event) or event == ev.OTP_LOGIN:
			frappe.log_error(
				title="Hostel360: notification for an unknown event",
				message=f"notify() was called with event {event!r}; nothing was sent.",
			)
			return

		cleaned = clean_recipients(recipients)
		if not cleaned:
			return

		channel_list = None
		if channels:
			channel_list = sorted({c for c in (ev.normalize_channel(x) for x in channels) if c})
			if not channel_list:
				return

		plain_context = rendering.to_plain(context or {})
		for start in range(0, len(cleaned), RECIPIENTS_PER_JOB):
			frappe.enqueue(
				DELIVER_METHOD,
				queue="short",
				enqueue_after_commit=not frappe.in_test,
				now=frappe.in_test,
				# frappe.enqueue has its own ``event`` argument, so the event travels under another name.
				notification_event=event,
				recipients=cleaned[start : start + RECIPIENTS_PER_JOB],
				context=plain_context,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
				channels=channel_list,
			)
	except Exception:
		frappe.log_error(
			title=f"Hostel360: could not queue {event} notification", message=frappe.get_traceback()
		)


def deliver(
	notification_event: str,
	recipients: list[dict],
	context: dict | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	channels: list[str] | None = None,
) -> None:
	"""Background job: render and send the event to each recipient on every enabled channel."""
	event = notification_event
	try:
		settings = get_settings()
		templates = _templates_for(event, channels, settings)
		if not templates:
			return
		job = _Delivery(event, context or {}, reference_doctype, reference_name, settings, templates)
		for recipient in clean_recipients(recipients):
			try:
				job.deliver_to(recipient)
			except Exception:
				frappe.log_error(title=f"Hostel360: {event} delivery error", message=frappe.get_traceback())
	except Exception:
		frappe.log_error(title=f"Hostel360: {event} delivery job failed", message=frappe.get_traceback())


def send_otp_message(mobile: str, otp: str, channel: str) -> bool:
	"""Send a sign-in code synchronously. Returns True when the provider accepted it.

	The code itself is never written to the Message Log, the Error Log or any log file;
	provider responses are scrubbed of it before they are stored.
	"""
	channel = ev.normalize_channel(channel)
	if channel not in ev.MOBILE_CHANNELS or not otp:
		return False
	mobile = _safe_mobile(mobile)
	if not mobile:
		return False

	settings = get_settings()
	template = _otp_template(channel)
	if not template:
		return False

	expiry_minutes = max(1, math.ceil((cint(settings.get("otp_expiry_seconds")) or 300) / 60))
	log = _new_log(channel, ev.OTP_LOGIN, mobile, None, None, None, template.get("name"))
	log["message"] = "Sign-in code message (the code is never stored)."
	try:
		ctx = rendering.build_context({"otp": otp, "expiry_minutes": expiry_minutes})
		if channel == ev.SMS:
			result = sms_provider.send_sms(
				mobile,
				rendering.render(template.get("body"), ctx).strip(),
				dlt_template_id=template.get("dlt_template_id"),
				msg91_template_id=template.get("msg91_template_id"),
				variables={"otp": otp, "OTP": otp, "expiry_minutes": expiry_minutes},
				parameters=rendering.render_parameters(template.get("template_parameters"), ctx),
				settings=settings,
			)
		else:
			result = whatsapp_provider.send_authentication_code(
				mobile,
				template.get("whatsapp_template_name"),
				template.get("whatsapp_language"),
				otp,
				settings=settings,
			)
	except Exception as e:
		result = DeliveryResult.failed(channel, f"{type(e).__name__}: {e}")

	if result.error:
		result.error = result.error.replace(otp, "******")
	_finish_log(log, result)
	record_outcome(result)
	return bool(result.ok)


def send_direct(channel: str, to: str, subject: str | None, body: str) -> bool:
	"""Send a message without a template (operations alerts, test messages).

	SMS uses the operations alert DLT template and WhatsApp the operations alert template
	configured in Messaging Settings, since both networks refuse free-form
	business-initiated text.
	"""
	channel = ev.normalize_channel(channel)
	if channel not in (ev.SMS, ev.WHATSAPP, ev.EMAIL) or not (to and body):
		return False

	settings = get_settings()
	address = _safe_mobile(to) if channel in ev.MOBILE_CHANNELS else cstr(to).strip().lower()
	if not address:
		return False

	log = _new_log(channel, ev.DIRECT_MESSAGE, address, None, None, None, None)
	log["subject"] = cstr(subject)[:SUBJECT_LIMIT]
	log["message"] = cstr(body)[:MESSAGE_TEXT_LIMIT]
	payload = {"subject": subject or "", "body": body}
	try:
		result = _send_direct(channel, address, subject, body, settings)
	except Exception as e:
		result = DeliveryResult.failed(channel, f"{type(e).__name__}: {e}", retryable=True)
	_finish_log(log, result, address=address, payload=payload)
	record_outcome(result, allow_alert=False)
	return bool(result.ok)


def retry_failed_messages() -> None:
	"""Scheduled every 15 minutes: retry failed messages that are due another attempt."""
	now = now_datetime()
	cutoff = add_to_date(now, hours=-RETRY_WINDOW_HOURS)
	names = frappe.get_all(
		"Message Log",
		filters={
			"status": "Failed",
			"attempts": ("<", MAX_ATTEMPTS),
			"payload": ("is", "set"),
			"next_retry_on": ("<=", now),
			"creation": (">=", cutoff),
		},
		pluck="name",
		order_by="next_retry_on asc",
		limit=RETRY_BATCH_SIZE,
	)
	for name in names:
		try:
			retry_message(name)
			if not frappe.in_test:
				# Each retry has already gone out to a provider; keep its log row in step
				# even if a later message in this batch fails.
				frappe.db.commit()  # nosemgrep
		except Exception:
			if not frappe.in_test:
				frappe.db.rollback()
			frappe.log_error(title="Hostel360: message retry failed", message=frappe.get_traceback())

	_purge_stale_payloads(cutoff)


def retry_message(name: str, force: bool = False) -> DeliveryResult | None:
	"""Send a failed message again from its stored payload."""
	# Row lock: a manual "Retry Now" racing the scheduled retry must not send twice.
	log = frappe.get_doc("Message Log", name, for_update=True)
	if log.status != "Failed" or not log.payload:
		return None
	if not force and cint(log.attempts) >= MAX_ATTEMPTS:
		return None

	payload = json.loads(log.payload)
	address = decrypt(payload["to"])
	settings = get_settings()
	try:
		if log.event == ev.DIRECT_MESSAGE:
			result = _send_direct(log.channel, address, payload.get("subject"), payload.get("body"), settings)
		else:
			result = _send_rendered(
				log.channel, address, payload, log.reference_doctype, log.reference_name, settings
			)
	except Exception as e:
		result = DeliveryResult.failed(log.channel, f"{type(e).__name__}: {e}", retryable=True)

	attempts = cint(log.attempts) + 1
	updates = {
		"attempts": attempts,
		"provider": result.provider,
		"provider_message_id": result.message_id,
		"error": result.error,
	}
	if result.ok:
		updates.update(
			status="Skipped" if result.skipped else "Sent",
			sent_on=None if result.skipped else now_datetime(),
			payload=None,
			next_retry_on=None,
		)
	elif result.retryable and attempts < MAX_ATTEMPTS:
		updates.update(status="Failed", next_retry_on=_next_retry_on(attempts))
	else:
		updates.update(status="Failed", payload=None, next_retry_on=None)
	frappe.db.set_value("Message Log", name, updates)
	record_outcome(result, allow_alert=log.event != ev.DIRECT_MESSAGE)
	return result


# ---------------------------------------------------------------- recipients


def recipients_for_student(
	student: str, include_student: bool = True, include_guardians: bool = False
) -> list[dict]:
	"""The student and/or their linked guardians (rejected guardians are left out)."""
	if not student:
		return []
	row = frappe.db.get_value(
		"Hostel Student",
		student,
		["name", "full_name", "first_name", "mobile_number", "email", "user"],
		as_dict=True,
	)
	if not row:
		return []

	recipients = []
	if include_student:
		recipients.append(
			{
				"user": row.user,
				"mobile": row.mobile_number,
				"email": row.email,
				"name": row.full_name or row.first_name,
				"kind": "student",
			}
		)
	if include_guardians:
		links = frappe.get_all(
			"Hostel Student Guardian",
			filters={"parent": student, "parenttype": "Hostel Student"},
			fields=["guardian"],
			order_by="is_primary desc, idx asc",
		)
		for link in links:
			if not link.guardian:
				continue
			guardian = frappe.db.get_value(
				"Hostel Guardian",
				link.guardian,
				["guardian_name", "mobile_number", "email", "user", "validation_status"],
				as_dict=True,
			)
			if not guardian or guardian.validation_status == "Rejected":
				continue
			recipients.append(
				{
					"user": guardian.user,
					"mobile": guardian.mobile_number,
					"email": guardian.email,
					"name": guardian.guardian_name,
					"kind": "parent",
				}
			)
	return clean_recipients(recipients)


def recipients_for_users(users: list[str]) -> list[dict]:
	"""Enabled users with their e-mail and the best mobile number we hold for them."""
	names = [u for u in dict.fromkeys(cstr(u).strip() for u in users or []) if u and u != "Guest"]
	if not names:
		return []
	rows = frappe.get_all(
		"User",
		filters={"name": ("in", names), "enabled": 1},
		fields=["name", "full_name", "email", "mobile_no", "user_type"],
	)
	mobiles = _mobiles_for_users([r.name for r in rows if not r.mobile_no])
	recipients = []
	for row in rows:
		email = row.email if row.email and not email_provider.is_placeholder_address(row.email) else None
		recipients.append(
			{
				"user": row.name,
				"mobile": row.mobile_no or mobiles.get(row.name),
				"email": email,
				"name": row.full_name,
				"kind": "staff" if row.user_type == "System User" else None,
			}
		)
	return clean_recipients(recipients)


def recipients_for_approvers(role: str, hostel: str | None = None) -> list[dict]:
	"""Holders of ``role`` who may act for ``hostel``, plus every warden and administrator.

	A role holder qualifies when they have a User Permission for the hostel, or no hostel
	restriction at all (staff without User Permissions see every hostel).
	"""
	holders = _users_with_roles([role]) if role else []
	if hostel and holders:
		restricted = set(
			frappe.get_all(
				"User Permission",
				filters={"allow": "Hostel", "user": ("in", holders)},
				pluck="user",
				distinct=True,
			)
		)
		permitted = set(
			frappe.get_all(
				"User Permission",
				filters={"allow": "Hostel", "for_value": hostel, "user": ("in", holders)},
				pluck="user",
				distinct=True,
			)
		)
		holders = [u for u in holders if u not in restricted or u in permitted]
	users = list(dict.fromkeys([*holders, *_users_with_roles([WARDEN_ROLE, ADMIN_ROLE])]))
	return recipients_for_users(users)


def clean_recipients(recipients) -> list[dict]:
	"""Normalise recipient dicts and drop empty or duplicate ones."""
	if isinstance(recipients, dict):
		recipients = [recipients]
	cleaned, seen = [], set()
	for recipient in recipients or []:
		if not isinstance(recipient, dict):
			continue
		user = cstr(recipient.get("user")).strip() or None
		if user == "Guest":
			user = None
		mobile = _safe_mobile(recipient.get("mobile"))
		email = cstr(recipient.get("email")).strip().lower() or None
		if email and not validate_email_address(email):
			email = None
		if not (user or mobile or email):
			continue
		key = (user, mobile, email)
		if key in seen:
			continue
		seen.add(key)
		cleaned.append(
			{
				"user": user,
				"mobile": mobile,
				"email": email,
				"name": cstr(recipient.get("name")).strip() or None,
				"kind": cstr(recipient.get("kind")).strip() or None,
			}
		)
	return cleaned


# ---------------------------------------------------------------- delivery internals


class _Delivery:
	"""One deliver() job: shared templates and per-channel de-duplication of addresses."""

	def __init__(self, event, context, reference_doctype, reference_name, settings, templates):
		self.event = event
		self.context = context
		self.reference_doctype = reference_doctype
		self.reference_name = reference_name
		self.settings = settings
		self.templates = templates
		# A student and a parent may share a phone; send each address one copy.
		self.seen = {channel: set() for channel in ev.CHANNELS}

	def deliver_to(self, recipient: dict) -> None:
		mobile_channels = [c for c in (ev.WHATSAPP, ev.SMS) if c in self.templates]
		if recipient.get("mobile") and mobile_channels:
			if cint(self.settings.get("prefer_whatsapp")) and len(mobile_channels) == 2:
				result = self.send(ev.WHATSAPP, recipient, allow_retry=False)
				if result is not None and not result.ok:
					self.send(ev.SMS, recipient)
			else:
				for channel in mobile_channels:
					self.send(channel, recipient)

		if ev.EMAIL in self.templates and recipient.get("email"):
			self.send(ev.EMAIL, recipient)
		if ev.IN_APP in self.templates and recipient.get("user"):
			self.send(ev.IN_APP, recipient)

	def send(self, channel: str, recipient: dict, allow_retry: bool = True) -> DeliveryResult | None:
		address = {
			ev.SMS: recipient.get("mobile"),
			ev.WHATSAPP: recipient.get("mobile"),
			ev.EMAIL: recipient.get("email"),
			ev.IN_APP: recipient.get("user"),
		}[channel]
		if not address or address in self.seen[channel]:
			return None
		if channel == ev.EMAIL and email_provider.is_placeholder_address(address):
			return None
		self.seen[channel].add(address)

		template = self.templates[channel]
		log = _new_log(
			channel,
			self.event,
			address,
			recipient.get("user"),
			self.reference_doctype,
			self.reference_name,
			template.name,
		)
		try:
			rendered = render_for_channel(self.event, template, channel, self.context, recipient)
		except rendering.TemplateRenderError as e:
			frappe.log_error(
				title=f"Hostel360: message template {template.name} could not be rendered",
				message=str(e),
			)
			result = DeliveryResult.failed("Template", f"Template error: {e}")
			_finish_log(log, result)
			return result

		log["subject"] = rendered["subject"][:SUBJECT_LIMIT]
		log["message"] = _stored_text(channel, rendered["body"])
		if channel == ev.IN_APP:
			result = _create_notification(
				address,
				rendered["subject"],
				rendered["body"],
				self.event,
				self.reference_doctype,
				self.reference_name,
				recipient,
			)
		else:
			payload = {
				"subject": rendered["subject"],
				"body": rendered["body"],
				"parameters": rendered["parameters"],
				"variables": rendered["variables"],
				"dlt_template_id": template.dlt_template_id,
				"msg91_template_id": template.msg91_template_id,
				"whatsapp_template_name": template.whatsapp_template_name,
				"whatsapp_language": template.whatsapp_language,
			}
			result = _send_rendered(
				channel, address, payload, self.reference_doctype, self.reference_name, self.settings
			)
			_finish_log(log, result, address=address, payload=payload if allow_retry else None)
			record_outcome(result)
			return result

		_finish_log(log, result)
		return result


def render_for_channel(
	event: str, template, channel: str, context: dict, recipient: dict | None = None
) -> dict:
	"""Subject, body, provider parameters and named variables for one recipient."""
	html_safe = channel in (ev.EMAIL, ev.IN_APP)
	ctx = rendering.build_context(context, recipient, html_safe=html_safe)
	# E-mail subjects are plain text headers, so they get the unescaped values.
	subject_ctx = rendering.build_context(context, recipient) if channel == ev.EMAIL else ctx

	subject = rendering.plain_text(rendering.render(template.get("subject"), subject_ctx))
	body = rendering.render(template.get("body"), ctx).strip()
	parameters, variables = [], {}
	if channel in ev.MOBILE_CHANNELS:
		parameters = rendering.render_parameters(template.get("template_parameters"), ctx)
		variables = {
			key: ctx[key]
			for key in rendering.available_variables(event)
			if isinstance(ctx.get(key), str | int | float) and not isinstance(ctx.get(key), bool)
		}
	return {"subject": subject, "body": body, "parameters": parameters, "variables": variables}


def _templates_for(event: str, channels: list[str] | None, settings) -> dict:
	filters = {"event": event, "enabled": 1}
	if channels:
		filters["channel"] = ("in", channels)
	rows = frappe.get_all("Message Template", filters=filters, fields=list(TEMPLATE_FIELDS))
	templates = {}
	for row in rows:
		if event in ev.MOBILE_ONLY_EVENTS and row.channel not in ev.MOBILE_CHANNELS:
			continue
		if _channel_enabled(settings, row.channel):
			templates[row.channel] = row
	return templates


def _channel_enabled(settings, channel: str) -> bool:
	return {
		ev.SMS: cint(settings.get("sms_enabled")),
		ev.WHATSAPP: cint(settings.get("whatsapp_enabled")),
		ev.EMAIL: cint(settings.get("email_enabled")),
		ev.IN_APP: 1,
	}.get(channel, 0) == 1


def _otp_template(channel: str) -> dict | None:
	"""The sign-in code template; falls back to the built-in wording if it was deleted."""
	name = f"{ev.OTP_LOGIN}-{channel}"
	row = frappe.db.get_value("Message Template", name, [*TEMPLATE_FIELDS, "enabled"], as_dict=True)
	if row:
		return row if cint(row.enabled) else None
	default = get_default_template(ev.OTP_LOGIN, channel)
	return frappe._dict(default, name=None) if default else None


def _send_rendered(channel, address, payload, reference_doctype, reference_name, settings) -> DeliveryResult:
	if channel == ev.SMS:
		return sms_provider.send_sms(
			address,
			payload.get("body"),
			dlt_template_id=payload.get("dlt_template_id"),
			msg91_template_id=payload.get("msg91_template_id"),
			variables=payload.get("variables"),
			parameters=payload.get("parameters"),
			settings=settings,
		)
	if channel == ev.WHATSAPP:
		return whatsapp_provider.send_template(
			address,
			payload.get("whatsapp_template_name"),
			payload.get("whatsapp_language"),
			payload.get("parameters"),
			settings=settings,
		)
	if channel == ev.EMAIL:
		return email_provider.send_email(
			address,
			payload.get("subject"),
			payload.get("body"),
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			settings=settings,
		)
	return DeliveryResult.failed(channel, f"Channel {channel} cannot be retried.")


def _send_direct(channel, address, subject, body, settings) -> DeliveryResult:
	if channel == ev.EMAIL:
		return email_provider.send_email(address, subject or "", _as_html(body), settings=settings)
	if channel == ev.SMS:
		return sms_provider.send_alert_sms(address, subject, body, settings=settings)
	text = " ".join(part for part in (subject, body) if part)
	return whatsapp_provider.send_template(
		address,
		settings.get("alert_whatsapp_template"),
		settings.get("alert_whatsapp_language"),
		[text],
		settings=settings,
	)


def _create_notification(
	user, subject, body, event, reference_doctype, reference_name, recipient
) -> DeliveryResult:
	info = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not info or not cint(info.enabled):
		return DeliveryResult.skip(NOTIFICATION_PROVIDER, "The user is disabled or does not exist.")

	is_portal_user = info.user_type == "Website User" or recipient.get("kind") in ("student", "parent")
	link = None
	if is_portal_user:
		link = PORTAL_LINKS.get(event)
		if reference_doctype == "Hostel Grievance" and reference_name and link:
			link = f"{link}/{reference_name}"

	subject = (subject or ev.EVENT_LABELS.get(event, ""))[:SUBJECT_LIMIT]
	doc = frappe.new_doc("Notification Log")
	doc.update(
		{
			"type": "Alert",
			"for_user": user,
			"title": subject,
			"subject": subject,
			"description": body,
			"email_content": body,
			"document_type": reference_doctype,
			"document_name": reference_name,
			"link": link,
		}
	)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return DeliveryResult.sent(NOTIFICATION_PROVIDER, doc.name)


def _new_log(channel, event, address, user, reference_doctype, reference_name, template) -> dict:
	return {
		"doctype": "Message Log",
		"channel": channel,
		"event": event,
		"status": "Queued",
		"recipient": _mask(channel, address),
		"recipient_user": user or None,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"template": template or None,
		"attempts": 0,
	}


def _finish_log(
	log: dict, result: DeliveryResult, *, address: str | None = None, payload: dict | None = None
) -> None:
	log["provider"] = result.provider
	log["provider_message_id"] = result.message_id
	log["attempts"] = 1
	if result.skipped:
		log["status"] = "Skipped"
		log["error"] = result.error
	elif result.ok:
		log["status"] = "Sent"
		log["sent_on"] = now_datetime()
	else:
		log["status"] = "Failed"
		log["error"] = result.error
		if result.retryable and payload is not None and address:
			log["payload"] = json.dumps({**payload, "to": encrypt(address)}, default=str)
			log["next_retry_on"] = _next_retry_on(1)
	try:
		frappe.get_doc(log).insert(ignore_permissions=True, ignore_links=True)
	except Exception:
		frappe.log_error(title="Hostel360: could not write Message Log", message=frappe.get_traceback())


def _next_retry_on(attempts: int):
	return add_to_date(now_datetime(), minutes=RETRY_BASE_MINUTES * (2 ** max(attempts - 1, 0)))


def _purge_stale_payloads(cutoff) -> None:
	"""Drop stored delivery details of messages that will not be retried any more."""
	log = frappe.qb.DocType("Message Log")
	(
		frappe.qb.update(log)
		.set(log.payload, None)
		.set(log.next_retry_on, None)
		.where(log.payload.isnotnull())
		.where((log.creation < cutoff) | (log.attempts >= MAX_ATTEMPTS) | (log.status != "Failed"))
	).run()


def _mask(channel: str, address: str | None) -> str:
	if not address:
		return ""
	if channel in ev.MOBILE_CHANNELS:
		return mask_mobile(address)
	return mask_email(address)


def _stored_text(channel: str, body: str) -> str:
	text = strip_html(body) if channel in (ev.EMAIL, ev.IN_APP) else body
	text = html.unescape(text) if channel in (ev.EMAIL, ev.IN_APP) else text
	return cstr(text).strip()[:MESSAGE_TEXT_LIMIT]


def _as_html(body: str) -> str:
	text = cstr(body)
	if "<" in text and ">" in text:
		return text
	return "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"


def _safe_mobile(value) -> str | None:
	if not value:
		return None
	try:
		return normalize_mobile(value)
	except frappe.ValidationError:
		frappe.clear_last_message()
		return None


def _users_with_roles(roles: list[str]) -> list[str]:
	if not roles:
		return []
	has_role = frappe.qb.DocType("Has Role")
	user = frappe.qb.DocType("User")
	rows = (
		frappe.qb.from_(has_role)
		.join(user)
		.on(user.name == has_role.parent)
		.select(has_role.parent)
		.distinct()
		.where(has_role.parenttype == "User")
		.where(has_role.role.isin(roles))
		.where(user.enabled == 1)
		.where(user.name.notin(["Administrator", "Guest"]))
	).run(pluck=True)
	return sorted(rows)


def _mobiles_for_users(users: list[str]) -> dict:
	"""Mobile numbers for users whose User record has none (staff, students, guardians)."""
	if not users:
		return {}
	found = {}
	for doctype in ("Hostel Staff", "Hostel Student", "Hostel Guardian"):
		if len(found) == len(users) or not frappe.db.exists("DocType", doctype):
			continue
		pending = [u for u in users if u not in found]
		for row in frappe.get_all(
			doctype, filters={"user": ("in", pending)}, fields=["user", "mobile_number"]
		):
			if row.mobile_number and row.user not in found:
				found[row.user] = row.mobile_number
	return found
