# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from hostel360.hostel_communication import events as ev
from hostel360.hostel_communication import rendering

SMS_SINGLE_SEGMENT = 160
FINGERPRINT_FIELDS = (
	"enabled",
	"subject",
	"body",
	"template_parameters",
	"dlt_template_id",
	"msg91_template_id",
	"whatsapp_template_name",
	"whatsapp_language",
)


class MessageTemplate(Document):
	def autoname(self):
		# English templates keep the plain {event}-{channel} name from the DocType's naming
		# rule; translations get the language as a suffix.
		language = self.language or ev.DEFAULT_LANGUAGE
		if language != ev.DEFAULT_LANGUAGE and self.event and self.channel:
			self.name = ev.template_name(self.event, self.channel, language)

	def validate(self):
		if not ev.is_valid_event(self.event):
			frappe.throw(_("Choose an event for this template."))
		if self.event == ev.OTP_LOGIN and self.channel not in ev.OTP_CHANNELS:
			frappe.throw(_("Sign-in codes can only be sent by SMS, WhatsApp or e-mail."))

		self.normalise()
		self.validate_syntax()
		self.validate_channel_requirements()
		if not self.flags.from_install_defaults:
			self.warn_about_sms_length()

	def normalise(self):
		"""Tidy the editable fields; also used to fingerprint the shipped wording."""
		if ev.is_valid_event(self.event):
			self.event_label = ev.EVENT_LABELS[self.event]
			self.available_variables = ", ".join(rendering.available_variables(self.event))
		self.enabled = cint(self.enabled)
		self.language = self.language or ev.DEFAULT_LANGUAGE
		if self.language != ev.DEFAULT_LANGUAGE:
			# A translation is sent as that language's version of the WhatsApp template.
			self.whatsapp_language = self.language
		self.body = (self.body or "").strip()
		self.subject = (self.subject or "").strip() or None
		self.template_parameters = (
			"\n".join(line.strip() for line in (self.template_parameters or "").splitlines() if line.strip())
			or None
		)
		for field in ("dlt_template_id", "msg91_template_id", "whatsapp_template_name"):
			self.set(field, (self.get(field) or "").strip() or None)
		if self.whatsapp_template_name:
			self.whatsapp_template_name = self.whatsapp_template_name.lower()

	def wording_fingerprint(self) -> str:
		"""Hash of everything an administrator might change (wording, IDs, on/off)."""
		values = {field: self.get(field) or "" for field in FINGERPRINT_FIELDS}
		values["enabled"] = cint(self.enabled)
		return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:20]

	def validate_syntax(self):
		parts = [(_("Subject"), self.subject), (_("Body"), self.body)]
		for line in (self.template_parameters or "").splitlines():
			# A parameter line is a bare expression unless it already contains Jinja tags.
			parts.append(
				(_("Template Parameters"), line if "{{" in line or "{%" in line else "{{ " + line + " }}")
			)
		for label, text in parts:
			if not text:
				continue
			try:
				rendering.compile_check(text)
				rendering.render(
					text,
					rendering.build_context(ev.get_sample_context(self.event), language=self.language),
				)
			except rendering.TemplateRenderError as e:
				frappe.throw(_("{0} has an error: {1}").format(label, e), title=_("Template Error"))

	def validate_channel_requirements(self):
		if not cint(self.enabled):
			return
		if self.channel in (ev.EMAIL, ev.IN_APP) and not self.subject:
			frappe.throw(_("A subject is required for {0} templates.").format(_(self.channel)))
		if self.channel == ev.WHATSAPP and not self.whatsapp_template_name:
			frappe.throw(_("Enter the approved WhatsApp template name."))

	def warn_about_sms_length(self):
		if self.channel != ev.SMS:
			return
		preview = self.render_preview()
		stats = preview["stats"]
		if (self.language or ev.DEFAULT_LANGUAGE) != ev.DEFAULT_LANGUAGE:
			# Tamil is always Unicode; what matters is how many parts are billed.
			if stats["segments"] > 1:
				frappe.msgprint(
					_(
						"Tamil SMS are sent as Unicode, 70 characters a part. With sample values this SMS "
						"is {0} characters and will be sent as {1} parts, each billed separately."
					).format(stats["length"], stats["segments"]),
					title=_("SMS Length"),
					indicator="orange",
				)
			return
		if stats["unicode"]:
			frappe.msgprint(
				_(
					"This SMS contains characters outside the standard SMS alphabet (for example the rupee "
					"symbol or curly quotes), so each part is limited to 70 characters. Use Rs. and plain "
					"quotes to keep it to a single SMS."
				),
				title=_("SMS Length"),
				indicator="orange",
			)
		elif stats["length"] > SMS_SINGLE_SEGMENT:
			frappe.msgprint(
				_("With sample values this SMS is {0} characters and will be sent as {1} parts.").format(
					stats["length"], stats["segments"]
				),
				title=_("SMS Length"),
				indicator="orange",
			)

	def render_preview(self, context: dict | None = None) -> dict:
		ctx_values = {**ev.get_sample_context(self.event), **(context or {})}
		sample_recipient = {"name": "Karthikeyan R", "kind": "student"}
		from hostel360.hostel_communication.dispatch import render_for_channel

		rendered = render_for_channel(self.event, self, self.channel, ctx_values, sample_recipient)
		stats = rendering.sms_stats(rendered["body"]) if self.channel == ev.SMS else None
		return {**rendered, "stats": stats}

	@frappe.whitelist()
	def preview(self, context: str | dict | None = None) -> dict:
		"""Render the template with sample values (or the given JSON context)."""
		self.check_permission("read")
		if isinstance(context, str) and context.strip():
			try:
				context = json.loads(context)
			except ValueError:
				frappe.throw(_("The sample context must be valid JSON."))
		if context is not None and not isinstance(context, dict):
			frappe.throw(_("The sample context must be a JSON object."))
		try:
			return self.render_preview(context)
		except rendering.TemplateRenderError as e:
			frappe.throw(_("The template could not be rendered: {0}").format(e), title=_("Template Error"))

	@frappe.whitelist()
	def get_default_wording(self) -> dict | None:
		"""The wording Hostel360 ships for this event, channel and language (to restore an edit)."""
		from hostel360.hostel_communication.default_templates import get_default_template

		self.check_permission("read")
		return get_default_template(self.event, self.channel, self.language)
