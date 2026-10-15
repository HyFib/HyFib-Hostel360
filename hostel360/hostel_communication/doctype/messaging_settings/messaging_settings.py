# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import ipaddress
import json
import re
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from hostel360.setup.roles import ADMIN_ROLE, OPERATIONS_ROLE

# (fieldname, label, minimum, maximum, default)
NUMERIC_LIMITS = (
	("otp_length", "Code Length", 4, 8, 6),
	("otp_expiry_seconds", "Code Valid For (Seconds)", 60, 1800, 300),
	("otp_max_attempts", "Wrong Attempts Allowed per Code", 1, 10, 5),
	("otp_resend_cooldown_seconds", "Resend Cooldown (Seconds)", 15, 600, 60),
	("otp_max_sends_per_hour", "Codes per Number per Hour", 1, 30, 5),
	("otp_max_sends_per_ip_per_hour", "Codes per IP Address per Hour", 1, 5000, 20),
	("provider_timeout_seconds", "Provider Timeout (Seconds)", 2, 60, 10),
	("failure_alert_threshold", "Alert After Consecutive Failures", 1, 100, 5),
)

SENDER_ID_RE = re.compile(r"^[A-Za-z0-9]{3,11}$")
API_VERSION_RE = re.compile(r"^v\d{1,3}\.\d{1,2}$")
TEST_MESSAGES_PER_HOUR = 10


class MessagingSettings(Document):
	def validate(self):
		self.apply_numeric_limits()
		self.validate_sms()
		self.validate_whatsapp()
		self.validate_trusted_networks()
		self.warn_about_otp_channels()

	def on_update(self):
		from hostel360.hostel_communication.rendering import clear_base_context_cache

		clear_base_context_cache()

	def apply_numeric_limits(self):
		for fieldname, label, minimum, maximum, default in NUMERIC_LIMITS:
			value = cint(self.get(fieldname))
			if not value:
				self.set(fieldname, default)
				continue
			if not minimum <= value <= maximum:
				frappe.throw(
					_("{0} must be between {1} and {2}.").format(_(label), minimum, maximum),
					title=_("Invalid Value"),
				)

	def validate_sms(self):
		if self.sms_sender_id:
			self.sms_sender_id = self.sms_sender_id.strip().upper()
			if not SENDER_ID_RE.match(self.sms_sender_id):
				frappe.throw(_("The Sender ID may contain only letters and digits (usually six letters)."))

		if self.sms_extra_params:
			try:
				params = json.loads(self.sms_extra_params)
			except ValueError:
				frappe.throw(_("Additional Parameters must be valid JSON."))
			if not isinstance(params, dict):
				frappe.throw(_('Additional Parameters must be a JSON object, for example {"route": "4"}.'))

		if self.sms_api_url:
			self.sms_api_url = self.sms_api_url.strip()
			validate_https_url(self.sms_api_url, _("SMS API URL"))

		if not cint(self.sms_enabled):
			return
		if not self.sms_auth_key:
			frappe.throw(_("Enter the SMS Auth Key before enabling SMS."))
		if self.sms_provider == "Generic HTTP":
			if not self.sms_api_url:
				frappe.throw(_("Enter the API URL of your SMS provider."))
			if not (self.sms_param_mobile and self.sms_param_message):
				frappe.throw(
					_("Mobile Parameter and Message Parameter are required for the generic provider.")
				)

	def validate_whatsapp(self):
		if self.whatsapp_api_version:
			self.whatsapp_api_version = self.whatsapp_api_version.strip()
			if not API_VERSION_RE.match(self.whatsapp_api_version):
				frappe.throw(_("Graph API Version must look like v21.0."))
		if self.whatsapp_phone_number_id:
			self.whatsapp_phone_number_id = self.whatsapp_phone_number_id.strip()
			if not self.whatsapp_phone_number_id.isdigit():
				frappe.throw(
					_("The WhatsApp Phone Number ID is a number shown in the Meta developer console.")
				)
		if cint(self.whatsapp_enabled) and not self.whatsapp_access_token:
			frappe.throw(_("Enter the WhatsApp Access Token before enabling WhatsApp."))

	def validate_trusted_networks(self):
		lines = []
		for line in (self.otp_trusted_networks or "").splitlines():
			entry = line.strip()
			if not entry:
				continue
			try:
				ipaddress.ip_network(entry, strict=False)
			except ValueError:
				frappe.throw(_("{0} is not a valid IP address or CIDR range.").format(frappe.bold(entry)))
			lines.append(entry)
		self.otp_trusted_networks = "\n".join(lines)

	def warn_about_otp_channels(self):
		missing = []
		if self.otp_channels in ("SMS", "SMS and WhatsApp") and not cint(self.sms_enabled):
			missing.append(_("SMS"))
		if self.otp_channels in ("WhatsApp", "SMS and WhatsApp") and not cint(self.whatsapp_enabled):
			missing.append(_("WhatsApp"))
		if missing and not cint(self.test_mode):
			frappe.msgprint(
				_(
					"Sign-in codes are set to go by {0}, which is not enabled. Students and parents "
					"will not receive codes until it is configured."
				).format(" / ".join(missing)),
				title=_("Sign-in Codes"),
				indicator="orange",
			)

	@frappe.whitelist(methods=["POST"])
	def send_test_message(self, channel: str, to: str) -> dict:
		"""Send a short test message so administrators can check the gateway set-up."""
		from hostel360.hostel_communication.dispatch import send_direct
		from hostel360.hostel_communication.events import normalize_channel

		frappe.only_for((ADMIN_ROLE, OPERATIONS_ROLE, "System Manager"))
		channel = normalize_channel(channel)
		if channel not in ("SMS", "WhatsApp", "Email"):
			frappe.throw(_("Choose SMS, WhatsApp or Email."))

		key = frappe.cache.make_key(f"hostel360:messaging:test_messages:{frappe.session.user}")
		count = cint(frappe.cache.incr(key))
		if count == 1:
			frappe.cache.expire(key, 3600)
		if count > TEST_MESSAGES_PER_HOUR:
			frappe.throw(
				_("You can send up to {0} test messages an hour.").format(TEST_MESSAGES_PER_HOUR),
				frappe.RateLimitExceededError,
			)

		ok = send_direct(
			channel,
			to,
			_("Hostel360 test message"),
			_("This is a test message from Hostel360. If you received it, messaging is set up correctly."),
		)
		return {
			"ok": ok,
			"message": _("Test message sent. Check the Message Log for the provider response.")
			if ok
			else _("The test message could not be sent. Open the latest Message Log entry to see why."),
		}


def validate_https_url(url: str, label: str) -> None:
	parsed = urlparse(url)
	allowed = ("https",) if not frappe.conf.developer_mode else ("https", "http")
	if parsed.scheme not in allowed or not parsed.netloc:
		frappe.throw(_("{0} must be a full https:// address.").format(label))
