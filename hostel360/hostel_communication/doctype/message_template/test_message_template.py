# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_communication import events as ev
from hostel360.hostel_communication import rendering
from hostel360.hostel_communication.default_templates import get_default_template, get_default_templates
from hostel360.hostel_communication.dispatch import render_for_channel
from hostel360.hostel_communication.setup import install_templates


def sample_recipient():
	return {"name": "Karthikeyan R", "kind": "student"}


class TestDefaultTemplates(IntegrationTestCase):
	def test_every_event_has_templates(self):
		names = set(frappe.get_all("Message Template", pluck="name"))
		for event in ev.EVENTS:
			channels = ev.MOBILE_CHANNELS if event in ev.MOBILE_ONLY_EVENTS else ev.CHANNELS
			for channel in channels:
				self.assertIn(f"{event}-{channel}", names)
		self.assertNotIn(f"{ev.OTP_LOGIN}-Email", names)
		self.assertEqual(set(ev.EVENTS), set(ev.EVENT_LABELS))
		self.assertEqual(set(ev.EVENTS), set(ev.EVENT_CONTEXT))

	def test_sms_defaults_fit_one_gsm_segment(self):
		for spec in get_default_templates():
			if spec["channel"] != ev.SMS:
				continue
			rendered = render_for_channel(
				spec["event"],
				frappe._dict(spec),
				ev.SMS,
				ev.get_sample_context(spec["event"]),
				sample_recipient(),
			)
			stats = rendering.sms_stats(rendered["body"])
			self.assertFalse(stats["unicode"], spec["event"])
			self.assertEqual(stats["segments"], 1, f"{spec['event']}: {stats['length']} characters")
			if spec["event"] != ev.OTP_LOGIN:
				# DLT matches the fixed text exactly; only the sign-in code's WebOTP line is optional.
				self.assertNotIn("{%", spec["body"], f"{spec['event']} SMS must not branch")

	def test_all_defaults_render_with_sample_values(self):
		for spec in get_default_templates():
			rendered = render_for_channel(
				spec["event"],
				frappe._dict(spec),
				spec["channel"],
				ev.get_sample_context(spec["event"]),
				sample_recipient(),
			)
			self.assertTrue(rendered["body"], spec)
			if spec["channel"] in (ev.EMAIL, ev.IN_APP):
				self.assertTrue(rendered["subject"], spec)
			if spec["channel"] in ev.MOBILE_CHANNELS:
				self.assertTrue(all(rendered["parameters"]), spec)

	def test_templates_tolerate_missing_values(self):
		for spec in get_default_templates():
			render_for_channel(spec["event"], frappe._dict(spec), spec["channel"], {}, None)


class TestMessageTemplateValidation(IntegrationTestCase):
	def template(self, **values):
		doc = frappe.get_doc("Message Template", f"{ev.FEE_REMINDER}-SMS")
		doc.update(values)
		return doc

	def test_syntax_errors_are_reported(self):
		with self.assertRaises(frappe.ValidationError):
			self.template(body="Dear {{ student_name ").save()
		with self.assertRaises(frappe.ValidationError):
			self.template(template_parameters="rupees(outstanding\ninvoice").save()

	def test_internal_attributes_are_blocked(self):
		with self.assertRaises(frappe.ValidationError):
			self.template(body="{{ student_name.__class__.__mro__ }}").save()

	def test_sign_in_codes_only_by_sms_or_whatsapp(self):
		doc = frappe.new_doc("Message Template")
		doc.update({"event": ev.OTP_LOGIN, "channel": ev.EMAIL, "subject": "Code", "body": "{{ otp }}"})
		with self.assertRaises(frappe.ValidationError):
			doc.insert()

	def test_whatsapp_needs_template_name(self):
		doc = frappe.get_doc("Message Template", f"{ev.FEE_REMINDER}-WhatsApp")
		doc.whatsapp_template_name = ""
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		doc.reload()
		doc.enabled = 0
		doc.whatsapp_template_name = ""
		doc.save()

	def test_email_needs_subject(self):
		doc = frappe.get_doc("Message Template", f"{ev.FEE_REMINDER}-Email")
		doc.subject = ""
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_duplicate_event_and_channel(self):
		doc = frappe.new_doc("Message Template")
		doc.update({"event": ev.FEE_REMINDER, "channel": ev.SMS, "body": "Hello"})
		with self.assertRaises(frappe.DuplicateEntryError):
			doc.insert()

	def test_preview_reports_sms_length(self):
		doc = self.template()
		preview = doc.preview()
		self.assertIn("Rs.12,500", preview["body"])
		self.assertEqual(preview["stats"]["segments"], 1)
		self.assertFalse(preview["stats"]["unicode"])

		unicode_preview = self.template(body="Fee of ₹{{ outstanding }} is due").preview()
		self.assertTrue(unicode_preview["stats"]["unicode"])

		custom = doc.preview('{"student_name": "Anitha", "outstanding": 999}')
		self.assertIn("Anitha", custom["body"])
		self.assertIn("Rs.999", custom["body"])

	def test_preview_rejects_bad_context(self):
		with self.assertRaises(frappe.ValidationError):
			self.template().preview("not json")

	def test_default_wording_is_available(self):
		wording = self.template().get_default_wording()
		self.assertEqual(wording["event"], ev.FEE_REMINDER)
		self.assertIn("Reminder", wording["body"])


class TestTemplateInstallation(IntegrationTestCase):
	def test_install_is_idempotent(self):
		self.assertEqual(install_templates(), {"created": 0, "updated": 0})

	def test_missing_templates_are_recreated(self):
		frappe.delete_doc("Message Template", f"{ev.MENU_PUBLISHED}-SMS", force=True)
		self.assertEqual(install_templates()["created"], 1)
		self.assertTrue(frappe.db.exists("Message Template", f"{ev.MENU_PUBLISHED}-SMS"))

	def test_untouched_templates_follow_new_wording_but_edited_ones_never_change(self):
		edited_name = f"{ev.PAYMENT_RECEIVED}-SMS"
		untouched_name = f"{ev.PAYMENT_RECEIVED}-In-App"
		edited = frappe.get_doc("Message Template", edited_name)
		edited.body = "Received {{ rupees(amount) }}. Thank you." + " -{{ institution_short_name }} Hostel"
		edited.save()

		def newer_defaults():
			rows = get_default_templates()
			for row in rows:
				if row["event"] == ev.PAYMENT_RECEIVED and row["channel"] in (ev.SMS, ev.IN_APP):
					row["body"] = "Updated wording {{ receipt_number }}"
			return rows

		with patch("hostel360.hostel_communication.setup.get_default_templates", side_effect=newer_defaults):
			result = install_templates()

		self.assertEqual(result["updated"], 1)
		self.assertEqual(
			frappe.db.get_value("Message Template", untouched_name, "body"),
			"Updated wording {{ receipt_number }}",
		)
		self.assertTrue(frappe.db.get_value("Message Template", edited_name, "body").startswith("Received"))

	def test_registered_template_is_treated_as_edited(self):
		name = f"{ev.PAYMENT_RECEIVED}-WhatsApp"
		frappe.db.set_value("Message Template", name, "whatsapp_template_name", "acgcet_payment_received")
		original = get_default_template(ev.PAYMENT_RECEIVED, ev.WHATSAPP)

		def newer_defaults():
			rows = get_default_templates()
			for row in rows:
				if row["event"] == ev.PAYMENT_RECEIVED and row["channel"] == ev.WHATSAPP:
					row["body"] = "Changed"
			return rows

		with patch("hostel360.hostel_communication.setup.get_default_templates", side_effect=newer_defaults):
			install_templates()
		self.assertEqual(frappe.db.get_value("Message Template", name, "body"), original["body"])
