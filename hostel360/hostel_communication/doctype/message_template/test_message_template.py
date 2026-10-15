# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.hostel_communication import events as ev
from hostel360.hostel_communication import rendering
from hostel360.hostel_communication.default_templates import get_default_template, get_default_templates
from hostel360.hostel_communication.dispatch import render_for_channel
from hostel360.hostel_communication.labels import label
from hostel360.hostel_communication.setup import install_templates

# Tamil SMS are Unicode; this is the most parts a shipped one may take with sample values.
TAMIL_SMS_MAX_PARTS = 4


def sample_recipient():
	return {"name": "Karthikeyan R", "kind": "student"}


class TestDefaultTemplates(IntegrationTestCase):
	def test_every_event_has_templates_in_every_language(self):
		names = set(frappe.get_all("Message Template", pluck="name"))
		for language in ev.LANGUAGES:
			for event in ev.EVENTS:
				for channel in ev.default_channels(event):
					self.assertIn(ev.template_name(event, channel, language), names)
		self.assertNotIn(f"{ev.OTP_LOGIN}-Email", names)
		self.assertNotIn(f"{ev.OTP_LOGIN}-Email-ta", names)
		self.assertEqual(set(ev.EVENTS), set(ev.EVENT_LABELS))
		self.assertEqual(set(ev.EVENTS), set(ev.EVENT_CONTEXT))

	def test_event_options_follow_the_catalogue(self):
		for doctype in ("Message Template", "Message Log"):
			options = frappe.get_meta(doctype).get_field("event").options.split("\n")
			for event in ev.EVENTS:
				self.assertIn(event, options, f"{doctype} is missing {event}")

	def test_new_events_reach_the_right_people(self):
		self.assertEqual(ev.default_channels(ev.BONAFIDE_REQUESTED), (ev.EMAIL, ev.IN_APP))
		for event in (ev.LEAVE_CHECKED_OUT, ev.LEAVE_RETURNED):
			self.assertEqual(ev.default_channels(event), ev.CHANNELS)
			# Guardians are addressed as parents.
			self.assertIn("Parent", get_default_template(event, ev.EMAIL)["body"])
		bonafide = get_default_template(ev.BONAFIDE_REQUESTED, ev.EMAIL)["body"]
		self.assertIn("desk_link", bonafide)

	def test_tamil_messaging_channels_ship_disabled(self):
		# Tamil SMS and WhatsApp must be registered with the operator before they can go out.
		for spec in get_default_templates([ev.TAMIL]):
			expected = 0 if spec["channel"] in (ev.SMS, ev.WHATSAPP) else 1
			self.assertEqual(spec["enabled"], expected, (spec["event"], spec["channel"]))
			if spec["channel"] == ev.WHATSAPP:
				self.assertEqual(spec["whatsapp_language"], ev.TAMIL)
				self.assertEqual(
					spec["whatsapp_template_name"],
					get_default_template(spec["event"], ev.WHATSAPP)["whatsapp_template_name"],
				)
		for spec in get_default_templates([ev.ENGLISH]):
			self.assertEqual(spec["enabled"], 1)

	def test_decision_subjects_name_the_student(self):
		context = {"student_name": "Meena S", "status": "Approved"}
		for event in (ev.LEAVE_DECIDED, ev.MESS_REDUCTION_DECIDED, ev.BONAFIDE_DECIDED):
			for language in ev.LANGUAGES:
				spec = frappe._dict(get_default_template(event, ev.IN_APP, language))
				rendered = render_for_channel(event, spec, ev.IN_APP, context, sample_recipient())
				self.assertIn("Meena S", rendered["subject"], (event, language))
			english = render_for_channel(
				event, frappe._dict(get_default_template(event, ev.IN_APP)), ev.IN_APP, {"status": "Approved"}
			)
			self.assertFalse(english["subject"].endswith(" for "), english["subject"])

	def test_tamil_templates_show_values_in_tamil(self):
		spec = frappe._dict(get_default_template(ev.LEAVE_DECIDED, ev.IN_APP, ev.TAMIL))
		rendered = render_for_channel(
			ev.LEAVE_DECIDED,
			spec,
			ev.IN_APP,
			{
				"student_name": "Meena S",
				"status": "Approved",
				"leave": "LV-1",
				"from_datetime": "2026-07-04 16:00:00",
				"to_datetime": "2026-07-07 19:00:00",
			},
		)
		self.assertIn(label("Approved", ev.TAMIL), rendered["subject"])
		self.assertNotIn("Approved", rendered["subject"])
		self.assertEqual(rendered["body"], "LV-1: 04-07-2026 04:00 PM முதல் 07-07-2026 07:00 PM வரை.")

	def test_tamil_labels_fit_a_dlt_variable(self):
		from hostel360.hostel_communication.labels import LABELS, MAX_LABEL_LENGTH

		for value, text in LABELS[ev.TAMIL].items():
			self.assertLessEqual(len(text), MAX_LABEL_LENGTH, value)
		self.assertEqual(label("Approved"), "Approved")
		self.assertEqual(label("Approved", ev.TAMIL), LABELS[ev.TAMIL]["Approved"])
		self.assertEqual(label("Something new", ev.TAMIL), "Something new")
		self.assertEqual(label(None, ev.TAMIL), "")

	def test_tamil_sms_are_unicode_without_branches(self):
		for spec in get_default_templates([ev.TAMIL]):
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
			self.assertTrue(stats["unicode"], spec["event"])
			self.assertLessEqual(
				stats["segments"], TAMIL_SMS_MAX_PARTS, f"{spec['event']}: {stats['length']} characters"
			)
			if spec["event"] != ev.OTP_LOGIN:
				self.assertNotIn("{%", spec["body"], f"{spec['event']} Tamil SMS must not branch")

	def test_sms_defaults_fit_one_gsm_segment(self):
		for spec in get_default_templates([ev.ENGLISH]):
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
		tamil = frappe.new_doc("Message Template")
		tamil.update({"event": ev.FEE_REMINDER, "channel": ev.SMS, "language": ev.TAMIL, "body": "வணக்கம்"})
		with self.assertRaises(frappe.DuplicateEntryError):
			tamil.insert()

	def test_translations_are_named_by_language(self):
		frappe.delete_doc("Message Template", f"{ev.MENU_PUBLISHED}-WhatsApp-ta", force=True)
		doc = frappe.new_doc("Message Template")
		doc.update(
			{
				"event": ev.MENU_PUBLISHED,
				"channel": ev.WHATSAPP,
				"language": ev.TAMIL,
				"body": "{{ mess }} உணவுப் பட்டியல்",
				"whatsapp_template_name": "h360_menu_published",
				"whatsapp_language": "en",
			}
		)
		doc.insert()
		self.assertEqual(doc.name, f"{ev.MENU_PUBLISHED}-WhatsApp-ta")
		# A Tamil row is always sent as the Tamil version of the WhatsApp template.
		self.assertEqual(doc.whatsapp_language, ev.TAMIL)

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
		tamil = frappe.get_doc("Message Template", f"{ev.FEE_REMINDER}-SMS-ta").get_default_wording()
		self.assertEqual(tamil["language"], ev.TAMIL)
		self.assertIn("நினைவூட்டல்", tamil["body"])

	def test_tamil_sms_preview_counts_unicode_parts(self):
		preview = frappe.get_doc("Message Template", f"{ev.PAYMENT_RECEIVED}-SMS-ta").preview()
		self.assertTrue(preview["stats"]["unicode"])
		self.assertGreaterEqual(preview["stats"]["segments"], 1)
		self.assertIn("RCPT-2026-00456", preview["body"])


class TestTemplateInstallation(IntegrationTestCase):
	def test_install_is_idempotent(self):
		self.assertEqual(install_templates(), {"created": 0, "updated": 0})

	def test_missing_templates_are_recreated(self):
		frappe.delete_doc("Message Template", f"{ev.MENU_PUBLISHED}-SMS", force=True)
		frappe.delete_doc("Message Template", f"{ev.MENU_PUBLISHED}-Email-ta", force=True)
		self.assertEqual(install_templates()["created"], 2)
		self.assertTrue(frappe.db.exists("Message Template", f"{ev.MENU_PUBLISHED}-SMS"))
		self.assertEqual(
			frappe.db.get_value("Message Template", f"{ev.MENU_PUBLISHED}-Email-ta", "language"), ev.TAMIL
		)

	def test_templates_saved_before_languages_count_as_english(self):
		name = f"{ev.MENU_PUBLISHED}-In-App"
		frappe.db.set_value("Message Template", name, "language", None, update_modified=False)
		install_templates()
		self.assertEqual(frappe.db.get_value("Message Template", name, "language"), ev.ENGLISH)

	def test_untouched_templates_follow_new_wording_but_edited_ones_never_change(self):
		edited_name = f"{ev.PAYMENT_RECEIVED}-SMS"
		untouched_name = f"{ev.PAYMENT_RECEIVED}-In-App"
		edited = frappe.get_doc("Message Template", edited_name)
		edited.body = "Received {{ rupees(amount) }}. Thank you." + " -{{ institution_short_name }} Hostel"
		edited.save()

		def newer_defaults():
			rows = get_default_templates()
			for row in rows:
				if (
					row["event"] == ev.PAYMENT_RECEIVED
					and row["channel"] in (ev.SMS, ev.IN_APP)
					and row["language"] == ev.ENGLISH
				):
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
				if (
					row["event"] == ev.PAYMENT_RECEIVED
					and row["channel"] == ev.WHATSAPP
					and row["language"] == ev.ENGLISH
				):
					row["body"] = "Changed"
			return rows

		with patch("hostel360.hostel_communication.setup.get_default_templates", side_effect=newer_defaults):
			install_templates()
		self.assertEqual(frappe.db.get_value("Message Template", name, "body"), original["body"])
