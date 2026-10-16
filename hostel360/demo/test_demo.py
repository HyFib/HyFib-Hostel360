# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Demonstration mode: nothing changes while it is off; while it is on, nothing leaves the
site, the demo accounts stay as published, and the pages say what the site is."""

import os
from contextlib import contextmanager, suppress
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from hostel360 import demo
from hostel360.auth import otp
from hostel360.auth.test_otp import TEST_IP, OTPTestCase, clear_otp_keys
from hostel360.demo import accounts
from hostel360.hostel_people.testing import make_desk_user
from hostel360.setup.roles import ADMIN_ROLE, OFFICE_ROLE, WARDEN_ROLE

MISSING = object()


@contextmanager
def demo_conf(on: bool = True, **values):
	"""Site config as a demonstration site has it (``on``), or with the keys but mode off."""
	values = {demo.DEMO_MODE_KEY: 1 if on else 0, **values}
	previous = {key: frappe.local.conf.get(key, MISSING) for key in values}
	frappe.local.conf.update(values)
	try:
		yield
	finally:
		for key, value in previous.items():
			if value is MISSING:
				frappe.local.conf.pop(key, None)
			else:
				frappe.local.conf[key] = value


def messaging_settings(**values):
	"""Messaging Settings as a live site has them: channels on, test mode off."""
	settings = frappe.get_doc("Messaging Settings")
	settings.update(
		{
			"sms_enabled": 1,
			"sms_provider": "MSG91",
			"whatsapp_enabled": 1,
			"email_enabled": 1,
			"test_mode": 0,
			**values,
		}
	)
	return settings


class TestDemoModeOff(IntegrationTestCase):
	"""With the key absent (or 0) every production behaviour is unchanged."""

	def test_mode_needs_the_site_config_key(self):
		with demo_conf(on=False, hostel360_demo_otp="135790"):
			self.assertFalse(demo.is_demo_site())
			self.assertIsNone(demo.portal_boot())
			self.assertIsNone(demo.desk_boot())
		with demo_conf():
			self.assertTrue(demo.is_demo_site())

	def test_fixed_code_refused_without_demo_mode(self):
		with demo_conf(on=False, developer_mode=0, hostel360_demo_otp="135790"):
			self.assertIsNone(otp.fixed_code())
			self.assertNotEqual(otp.generate_code(6), "135790")

	def test_providers_and_gateways_work_as_before(self):
		from hostel360.hostel_communication.providers import base, sms

		with demo_conf(on=False):
			self.assertIsNone(base.demo_mode_result("MSG91"))
			result = sms.send_sms(
				"9876543210", "Hello", dlt_template_id="1", settings=messaging_settings(test_mode=1)
			)
			self.assertTrue(result.skipped)
			self.assertNotEqual(result.error, demo.DEMO_REASON)

			from hostel360.hostel_fees.gateways import refuse_on_demo_site

			refuse_on_demo_site()

	def test_pages_and_boot_without_demo(self):
		from hostel360.setup.boot import extend_bootinfo
		from hostel360.www import demo as demo_page
		from hostel360.www import hostel, verify_certificate

		with demo_conf(on=False):
			self.assertIsNone(hostel.get_boot(None)["demo"])
			self.assertIsNone(verify_certificate.demo_banner())
			boot = frappe._dict()
			extend_bootinfo(boot)
			self.assertIsNone(boot.hostel360["demo"])
			with self.assertRaises(frappe.PageDoesNotExistError):
				demo_page.get_context(frappe._dict())

	def test_listed_accounts_unlocked_while_mode_is_off(self):
		user = make_desk_user(OFFICE_ROLE)
		with demo_conf(on=False, hostel360_demo_users=[user]):
			doc = frappe.get_doc("User", user)
			doc.append("roles", {"role": WARDEN_ROLE})
			doc.new_password = "Lantern-Mango-7781"
			doc.save(ignore_permissions=True)
			self.assertIn(WARDEN_ROLE, frappe.get_roles(user))
			accounts.protect_user_on_trash(doc)


class TestDemoSignIn(OTPTestCase):
	"""The published code, only on a demonstration site and only for numbers on record."""

	def test_published_code_for_registered_numbers(self):
		with demo_conf(hostel360_demo_otp="135790"):
			mobile = self.new_mobile(otp.KIND_STUDENT)
			otp.request_code(mobile, channel="sms", ip=TEST_IP)
			self.assertEqual(self.last_code(), "135790")
			result = otp.verify_code(mobile, "135790", ip=TEST_IP)
			self.assertTrue(result["ok"])

	def test_default_code(self):
		with demo_conf():
			self.assertEqual(otp.fixed_code(), demo.DEFAULT_OTP)

	def test_code_works_without_a_fresh_send(self):
		with demo_conf():
			mobile = self.new_mobile(otp.KIND_PARENT)
			result = otp.verify_code(mobile, demo.DEFAULT_OTP, ip=TEST_IP)
			self.assertTrue(result["ok"])
			self.assertEqual(result["kind"], otp.KIND_PARENT)
			self.assertEqual(self.sent, [])

	def test_code_never_works_for_unknown_numbers(self):
		with demo_conf():
			mobile = self.new_mobile()
			otp.request_code(mobile, channel="sms", ip=TEST_IP)
			self.assertEqual(self.sent, [])
			with self.assertRaises(frappe.AuthenticationError):
				otp.verify_code(mobile, demo.DEFAULT_OTP, ip=TEST_IP)

	def test_wrong_codes_still_count(self):
		with demo_conf():
			mobile = self.new_mobile(otp.KIND_STUDENT)
			with self.assertRaises(frappe.AuthenticationError) as caught:
				otp.verify_code(mobile, "111111", ip=TEST_IP)
			self.assertIn("4 more attempts", str(caught.exception))

	def test_rate_limits_stay_on(self):
		with demo_conf():
			mobile = self.new_mobile(otp.KIND_STUDENT)
			otp.request_code(mobile, channel="sms", ip=TEST_IP)
			with self.assertRaises(frappe.RateLimitExceededError):
				otp.request_code(mobile, channel="sms", ip=TEST_IP)
			ref = otp.mobile_ref(mobile)
			frappe.cache.set(otp._key(f"lock:{ref}"), "1", ex=60)
			with self.assertRaises(frappe.RateLimitExceededError):
				otp.verify_code(mobile, demo.DEFAULT_OTP, ip=TEST_IP)


class TestDemoMutes(IntegrationTestCase):
	"""On a demonstration site nothing reaches a provider, a gateway or a webhook."""

	def setUp(self):
		super().setUp()
		for target in ("requests.post", "requests.get"):
			patcher = patch(target, side_effect=AssertionError(f"{target} called on a demonstration site"))
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_every_provider_skips_with_the_reason(self):
		from hostel360.hostel_communication.providers import email, sms, whatsapp

		settings = messaging_settings()
		with demo_conf():
			results = [
				sms.send_sms("9876543210", "Hello", dlt_template_id="1", settings=settings),
				sms.send_alert_sms("9876543210", "Alert", "Body", settings=settings),
				whatsapp.send_template("9876543210", "fee_due", "en", ["x"], settings=settings),
				whatsapp.send_authentication_code("9876543210", "otp", "en", "123456", settings=settings),
				email.send_email("someone@example.com", "Subject", "Body", settings=settings),
				email.send_email_now("someone@example.com", "Subject", "Body", settings=settings),
			]
		for result in results:
			self.assertTrue(result.skipped)
			self.assertEqual(result.error, demo.DEMO_REASON)

	def test_message_log_records_skipped(self):
		from hostel360.hostel_communication import dispatch

		with (
			demo_conf(),
			patch("hostel360.hostel_communication.dispatch.get_settings", return_value=messaging_settings()),
		):
			dispatch.send_direct("Email", "someone@example.com", "Subject", "Body")
		log = frappe.get_last_doc("Message Log", filters={"event": "direct_message"})
		self.assertEqual(log.status, "Skipped")
		self.assertEqual(log.error, demo.DEMO_REASON)

	def test_sign_in_codes_are_not_sent(self):
		from hostel360.hostel_communication import dispatch

		with (
			demo_conf(),
			patch("hostel360.hostel_communication.dispatch.get_settings", return_value=messaging_settings()),
		):
			self.assertTrue(dispatch.send_otp_message("9876543210", "246810", "SMS"))
		log = frappe.get_last_doc("Message Log", filters={"event": "otp_login"})
		self.assertEqual(log.status, "Skipped")
		self.assertEqual(log.error, demo.DEMO_REASON)

	def test_operations_alerts_never_leave(self):
		from hostel360.hostel_operations import channels, health, notification

		message = {"severity": "Critical", "event": "raised", "title": "Disk", "alert": None}
		with demo_conf():
			self.assertEqual(
				channels.post_json("https://hooks.example.com/x", {}), (False, "demonstration site")
			)
			self.assertFalse(channels.send_email(["ops@example.com"], message)[0])
			self.assertFalse(channels.send_text("SMS", "9876543210", message)[0])
			targets = [
				{"channel_type": "Generic Webhook", "label": "Hook", "url": "https://hooks.example.com/x"}
			]
			results = notification.send_to_targets(targets, message, frappe._dict())
			self.assertEqual([r["ok"] for r in results], [False])
			self.assertEqual(results[0]["info"], demo.DEMO_REASON)
			settings = frappe._dict(heartbeat_url="https://hc-ping.example.com/abc", environment="Production")
			self.assertIsNone(health.send_heartbeat(settings, frappe.utils.now_datetime()))

	def test_payment_gateways_are_never_called(self):
		from hostel360.hostel_fees import gateways, webhooks
		from hostel360.hostel_fees.gateways.base import GatewayNotConfiguredError

		settings = frappe._dict(enabled=1, gateway="Razorpay", key_id="rzp_live_x", test_mode=0)
		with demo_conf():
			with self.assertRaises(GatewayNotConfiguredError) as caught:
				gateways.get_gateway(settings)
			self.assertIn("demonstration site", str(caught.exception))
			with self.assertRaises(GatewayNotConfiguredError):
				gateways.get_settlement_gateway("Razorpay", settings)
			self.assertFalse(gateways.is_gateway_configured(settings))
			self.assertIsNone(gateways.SettlementGateways(settings).get("Razorpay"))
			self.assertEqual(webhooks.handle_razorpay_webhook(b"{}", "sig")[0], 400)
			self.assertEqual(webhooks.handle_payu_webhook({"txnid": "x"})[0], 400)

	def test_portal_settings_say_demo(self):
		from hostel360.api import portal

		with demo_conf():
			self.assertTrue(portal._session_settings()["demo"])
		with demo_conf(on=False):
			self.assertFalse(portal._session_settings()["demo"])


class TestDemoAccounts(IntegrationTestCase):
	"""The published accounts keep their password, e-mail, roles and staff record."""

	def setUp(self):
		super().setUp()
		self.user = make_desk_user(OFFICE_ROLE)
		self.other = make_desk_user(OFFICE_ROLE)

	def demo(self):
		return demo_conf(hostel360_demo_users=[self.user])

	def test_password_email_roles_and_enabled_are_locked(self):
		with self.demo():
			for change in (
				lambda doc: setattr(doc, "new_password", "Lantern-Mango-7781"),
				lambda doc: doc.append("roles", {"role": WARDEN_ROLE}),
				lambda doc: setattr(doc, "enabled", 0),
				lambda doc: setattr(doc, "simultaneous_sessions", 1),
				lambda doc: setattr(doc, "api_key", "abc123"),
			):
				doc = frappe.get_doc("User", self.user)
				change(doc)
				with self.assertRaises(frappe.PermissionError):
					doc.save(ignore_permissions=True)

	def test_email_follows_the_login_id(self):
		# Frappe keeps a user's e-mail equal to the login ID; only a rename could change it.
		with self.demo():
			doc = frappe.get_doc("User", self.user)
			doc.email = f"x-{frappe.generate_hash(length=6)}@example.com"
			doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("User", self.user, "email"), self.user)

	def test_other_changes_and_other_accounts_are_allowed(self):
		with self.demo():
			doc = frappe.get_doc("User", self.user)
			doc.language = "ta"
			doc.save(ignore_permissions=True)
			other = frappe.get_doc("User", self.other)
			other.append("roles", {"role": WARDEN_ROLE})
			other.save(ignore_permissions=True)
			self.assertIn(WARDEN_ROLE, frappe.get_roles(self.other))

	def test_delete_rename_keys_and_password_change_refused(self):
		with self.demo():
			doc = frappe.get_doc("User", self.user)
			with self.assertRaises(frappe.PermissionError):
				accounts.protect_user_on_trash(doc)
			with self.assertRaises(frappe.PermissionError):
				accounts.protect_user_rename(doc, old=self.user, new="new@example.com")
			with self.assertRaises(frappe.PermissionError):
				accounts.generate_keys(self.user)
			frappe.set_user(self.user)
			try:
				with self.assertRaises(frappe.PermissionError):
					accounts.update_password("Lantern-Mango-7781", old_password="whatever")
			finally:
				frappe.set_user("Administrator")

	def test_demo_command_may_change_them(self):
		with self.demo(), patch.dict(frappe.flags, {accounts.SETUP_FLAG: True}):
			doc = frappe.get_doc("User", self.user)
			doc.append("roles", {"role": WARDEN_ROLE})
			doc.save(ignore_permissions=True)
		self.assertIn(WARDEN_ROLE, frappe.get_roles(self.user))

	def test_staff_record_protected(self):
		from hostel360.hostel_people.testing import make_staff

		staff = make_staff("Warden", email=f"demo-warden-{frappe.generate_hash(length=6)}@example.com")
		user = frappe.db.get_value("Hostel Staff", staff.name, "user")
		with demo_conf(hostel360_demo_users=[user]):
			doc = frappe.get_doc("Hostel Staff", staff.name)
			doc.designation = "Office Staff"
			with self.assertRaises(frappe.PermissionError):
				doc.save(ignore_permissions=True)
			doc = frappe.get_doc("Hostel Staff", staff.name)
			doc.mobile_number = "9876501234"
			doc.save(ignore_permissions=True)
			with self.assertRaises(frappe.PermissionError):
				frappe.delete_doc("Hostel Staff", staff.name, ignore_permissions=True)


class TestDemoPages(IntegrationTestCase):
	"""/demo, the portal's boot data, the desk boot and the certificate page."""

	ACCOUNTS = [
		{"user": "warden@demo.invalid", "kind": "staff", "role": WARDEN_ROLE, "name": "Dr. K. Senthilkumar"},
		{"user": "admin@demo.invalid", "kind": "staff", "role": ADMIN_ROLE, "name": "Revathi Sundaram"},
		{
			"user": "s1@demo.invalid",
			"kind": "student",
			"role": "Student, Boys Hostel I - fees due",
			"name": "Ragul Velu",
			"mobile": "9000000001",
			"hostel": "Boys Hostel I",
			"note": "dues",
		},
		{
			"user": "p1@demo.invalid",
			"kind": "parent",
			"role": "Parent of Ragul and Harini",
			"name": "Velu",
			"mobile": "9000000012",
			"note": "two_wards",
			"wards": ["Ragul", "Harini"],
		},
	]

	def demo(self):
		return demo_conf(
			hostel360_demo_password="Lotus-Kolam-4821",
			hostel360_demo_otp="246810",
			hostel360_demo_accounts=self.ACCOUNTS,
		)

	def test_demo_page_lists_the_sign_ins(self):
		from hostel360.www import demo as demo_page

		with self.demo():
			context = demo_page.get_context(frappe._dict())
			path = os.path.join(frappe.get_app_path("hostel360"), "www", "demo.html")
			with open(path, encoding="utf-8") as f:
				html = frappe.render_template(f.read(), context)
		self.assertIn("Lotus-Kolam-4821", html)
		self.assertIn("246810", html)
		self.assertIn("warden@demo.invalid", html)
		self.assertIn("9000000001", html)
		self.assertIn("Demonstration site", html)

	def test_portal_boot_and_desk_boot(self):
		from hostel360.setup.boot import extend_bootinfo
		from hostel360.www import hostel

		with self.demo():
			boot = hostel.get_boot(None)["demo"]
			desk = frappe._dict()
			extend_bootinfo(desk)
		self.assertEqual(boot["otp"], "246810")
		self.assertEqual([a["mobile"] for a in boot["accounts"]], ["9000000001", "9000000012"])
		self.assertEqual(boot["accounts"][1]["wards"], ["Ragul", "Harini"])
		# Staff accounts and the password are only on /demo, never in the portal page.
		self.assertNotIn("Lotus-Kolam-4821", frappe.as_json(boot))
		self.assertEqual(desk.hostel360["demo"], {"info_url": "/demo"})

	def test_certificate_page_banner(self):
		from hostel360.www import verify_certificate

		with self.demo():
			banner = verify_certificate.demo_banner()
		self.assertEqual(banner["url"], "/demo")
		self.assertIn("fictitious", banner["en"])


class TestRedate(IntegrationTestCase):
	def test_interval(self):
		from hostel360.demo.redate import interval

		self.assertEqual(interval("2026-10-01 22:00:00", "2026-10-03 06:00:00"), (32 * 3600, 2))

	def test_calendar_fields_stay(self):
		from hostel360.demo.redate import doctypes_to_shift, shift_assignments

		menu = " ".join(shift_assignments("Mess Menu", 60, 1))
		self.assertNotIn("week_start_date", menu)
		self.assertIn("published_on", menu)
		self.assertNotIn("from_date", " ".join(shift_assignments("Mess Bill Run", 60, 1)))
		self.assertIn("due_date", " ".join(shift_assignments("Mess Bill Run", 60, 1)))
		self.assertNotIn("Hostel Academic Year", doctypes_to_shift())
		self.assertIn("Home Visit Leave", doctypes_to_shift())

	def test_shift_moves_dates_and_times(self):
		from hostel360.demo.redate import shift_dates
		from hostel360.hostel_operations.metrics import record_metrics

		metric = f"demo_test_{frappe.generate_hash(length=6)}"
		record_metrics([{"metric": metric, "value": 1, "recorded_at": "2026-10-01 10:00:00"}], source="Test")
		shift_dates(3600, 1, doctypes=["Ops Metric Sample"])
		recorded = frappe.db.get_value("Ops Metric Sample", {"metric": metric}, "recorded_at")
		self.assertEqual(str(recorded), "2026-10-01 11:00:00")


class TestSnapshotFingerprint(IntegrationTestCase):
	def test_fingerprint_sees_every_record_a_migrate_applies(self):
		# A snapshot restored into code whose workspaces, dock or modules changed has to be
		# migrated, not only one whose DocTypes changed: otherwise every reset brings the old
		# desk back.
		import tempfile

		from hostel360.demo import snapshot

		with tempfile.TemporaryDirectory() as root:

			def write(relative_path: str, text: str) -> None:
				path = os.path.join(root, relative_path)
				os.makedirs(os.path.dirname(path), exist_ok=True)
				with open(path, "w", encoding="utf-8") as f:
					f.write(text)

			write("modules.txt", "Hostel Setup\n")
			write("hostel_setup/workspace/hostel_admin/hostel_admin.json", '{"roles": ["Mess Staff"]}')
			write("hostel_setup/doctype/hostel/hostel.json", '{"doctype": "DocType", "name": "Hostel"}')
			write("hostel_setup/doctype/hostel/hostel.py", "VALUE = 1\n")
			write("public/dist/build.json", "{}")

			with patch.object(frappe, "get_app_path", return_value=root):
				before = snapshot.sync_fingerprint("hostel360")

				write("hostel_setup/doctype/hostel/hostel.py", "VALUE = 2\n")
				write("public/dist/build.json", '{"changed": 1}')
				self.assertEqual(snapshot.sync_fingerprint("hostel360"), before)

				write("hostel_setup/workspace/hostel_admin/hostel_admin.json", '{"roles": []}')
				after_workspace = snapshot.sync_fingerprint("hostel360")
				self.assertNotEqual(after_workspace, before)

				write("modules.txt", "Hostel Setup\nHostel Gate\n")
				self.assertNotEqual(snapshot.sync_fingerprint("hostel360"), after_workspace)


def _job_must_not_run():
	raise AssertionError("the job ran although the site is being restored")


def forget_local_job():
	with suppress(AttributeError):
		del frappe.local.job


def _counts(rows: dict):
	"""A stand-in for frappe.db.count answering from ``rows`` (0 for everything else)."""

	def count(doctype, filters=None, *args, **kwargs):
		return rows.get(doctype, 0)

	return count


class TestSiteOrigin(IntegrationTestCase):
	"""Only a new site becomes a demonstration; a site it prepared keeps its origin."""

	def new_site(self, **rows):
		from hostel360.demo import site

		return (
			patch.object(frappe.db, "count", side_effect=_counts(rows)),
			patch.object(frappe, "get_installed_apps", return_value=["frappe", "hostel360"]),
			patch.object(site, "files_on_disk", return_value=0),
		)

	def test_a_new_site_is_accepted(self):
		from hostel360.demo.site import INSTALLED_RECORDS, ORIGIN_NEW_SITE, check_site_origin

		first, second, third = self.new_site(**dict.fromkeys(INSTALLED_RECORDS, 9))
		with demo_conf(on=False), first, second, third:
			self.assertEqual(check_site_origin(None), ORIGIN_NEW_SITE)

	def test_any_other_record_refuses_the_site(self):
		# A real guardian with no student and no staff passed the earlier register-number and
		# e-mail checks; every Hostel360 DocType counts now, including ones added later.
		from hostel360.demo.site import DemoSiteError, check_site_origin, records_a_new_site_does_not_have

		for doctype in ("Hostel Guardian", "Hostel Payment Transaction", "Message Log", "User", "Contact"):
			with self.subTest(doctype=doctype):
				first, second, third = self.new_site(**{doctype: 1})
				with demo_conf(on=False), first, second, third:
					self.assertEqual(records_a_new_site_does_not_have(), [(doctype, 1)])
					with self.assertRaises(DemoSiteError) as raised:
						check_site_origin(None)
					self.assertIn(doctype, str(raised.exception))

	def test_deleted_records_count_only_for_peoples_records(self):
		# A migrate on a new site deletes workspaces, icons or scheduled jobs the code dropped;
		# that leaves Deleted Document rows but no one's data.
		from hostel360.demo import site

		deleted = ["Desktop Icon", "Workspace", "Scheduled Job Type", "Hostel Guardian", "User"]

		def count(doctype, filters=None, *args, **kwargs):
			if doctype == "Deleted Document":
				return sum(1 for kind in deleted if kind in filters["deleted_doctype"][1])
			return 0

		with (
			patch.object(frappe.db, "count", side_effect=count),
			patch.object(frappe, "get_installed_apps", return_value=["frappe", "hostel360"]),
			patch.object(site, "files_on_disk", return_value=0),
		):
			self.assertEqual(site.records_a_new_site_does_not_have(), [("Deleted Document", 2)])

	def test_other_apps_and_uploads_refuse_the_site(self):
		from hostel360.demo import site

		first, _apps, _files = self.new_site(File=2)
		with (
			first,
			patch.object(frappe, "get_installed_apps", return_value=["frappe", "hostel360", "hrms"]),
			patch.object(site, "files_on_disk", return_value=3),
		):
			found = dict(site.records_a_new_site_does_not_have())
		self.assertEqual(found.get("File"), 2)
		self.assertIn(3, found.values())
		self.assertTrue(any("hrms" in what for what in found))

	def test_demonstrations_keep_their_origin(self):
		from hostel360.demo import site

		refuse = patch.object(site, "records_a_new_site_does_not_have", side_effect=AssertionError("checked"))
		with demo_conf(hostel360_demo_origin=site.ORIGIN_NEW_SITE), refuse:
			self.assertEqual(site.check_site_origin(None), site.ORIGIN_NEW_SITE)
		# prepared by an earlier release: demonstration mode and a snapshot
		with demo_conf(), refuse:
			self.assertEqual(site.check_site_origin({"taken_at": "x"}), site.ORIGIN_EARLIER_RELEASE)
		# the origin alone, with demonstration mode off, proves nothing
		found = patch.object(site, "records_a_new_site_does_not_have", return_value=[("User", 1)])
		with demo_conf(on=False, hostel360_demo_origin=site.ORIGIN_NEW_SITE), found as checked:
			with self.assertRaises(site.DemoSiteError):
				site.check_site_origin(None)
		checked.assert_called_once()

	def test_installed_records_hold_no_personal_data(self):
		from hostel360.demo.site import INSTALLED_RECORDS

		people = {"User", "Hostel Student", "Hostel Guardian", "Hostel Staff", "Contact"}
		for doctype in INSTALLED_RECORDS:
			for field in frappe.get_meta(doctype).fields:
				self.assertFalse(
					field.fieldtype == "Link" and field.options in people, f"{doctype}.{field.fieldname}"
				)
				self.assertNotIn(field.options, ("Email", "Phone"), f"{doctype}.{field.fieldname}")

	def test_files_written_by_frappe_do_not_count(self):
		import tempfile

		from hostel360.demo.site import files_on_disk

		with tempfile.TemporaryDirectory() as root:
			for path in ("public/files/website_theme/theme.css", "private/files/consent.pdf"):
				os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
				open(os.path.join(root, path), "w").close()
			with patch.object(frappe, "get_site_path", side_effect=lambda *parts: os.path.join(root, *parts)):
				self.assertEqual(files_on_disk(), 1)


class SnapshotSandbox(IntegrationTestCase):
	"""Snapshots taken and restored in a temporary folder, without a real dump or load."""

	def setUp(self):
		import gzip
		import tarfile
		import tempfile

		from hostel360.demo import snapshot

		self.snapshot = snapshot
		self.root = tempfile.mkdtemp()
		self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
		self.loaded = []
		self.before_dump = None

		class FakeDump:
			def __init__(self, *args, backup_path_db=None, **kwargs):
				self.path = backup_path_db

			def take_dump(dump):
				if self.before_dump:
					self.before_dump()
				with gzip.open(dump.path, "wt") as f:
					f.write(frappe.generate_hash())

		def archive(folder_name, target):
			with tarfile.open(target, "w:gz"):
				pass

		for target, value in (
			(snapshot, {"snapshot_dir": lambda: self.root, "_archive_files": archive}),
			(snapshot, {"app_versions": lambda: {"hostel360": "1"}, "_restore_files": lambda *a: None}),
		):
			for name, replacement in value.items():
				self.enterContext(patch.object(target, name, replacement))
		self.enterContext(patch("frappe.utils.backups.BackupGenerator", FakeDump))
		self.enterContext(
			patch("frappe.database.mariadb.setup_db.import_db_from_sql", side_effect=self.loaded.append)
		)
		for name in ("commit", "close"):
			self.enterContext(patch.object(frappe.db, name))
		self.enterContext(patch.object(frappe, "connect"))

	def take(self):
		return self.snapshot.take_snapshot({"hostel360_demo_mode": 1})

	def listing(self):
		return sorted(os.listdir(self.root))


class TestSnapshotGenerations(SnapshotSandbox):
	def test_failed_snapshot_keeps_the_previous_generation(self):
		snapshot = self.snapshot
		first = self.take()
		real_write, real_replace = snapshot._write_file, os.replace

		def fail_on(suffix):
			def write(path, text):
				if path.endswith(suffix):
					raise OSError("disk")
				return real_write(path, text)

			return write

		def replace_except_pointer(src, dst, *args, **kwargs):
			if str(dst).endswith(snapshot.POINTER):
				raise OSError("rename")
			return real_replace(src, dst, *args, **kwargs)

		failures = {
			"dump": patch(
				"frappe.utils.backups.BackupGenerator.take_dump", side_effect=OSError("dump"), create=True
			),
			"archive": patch.object(snapshot, "_archive_files", side_effect=OSError("archive")),
			"flush": patch.object(snapshot, "_flush", side_effect=OSError("flush")),
			"versions": patch.object(snapshot, "app_versions", side_effect=OSError("versions")),
			"manifest": patch.object(snapshot, "_write_file", side_effect=fail_on(snapshot.MANIFEST)),
			"pointer": patch.object(snapshot.os, "replace", side_effect=replace_except_pointer),
		}
		for step, failure in failures.items():
			with self.subTest(step=step):
				with failure, self.assertRaises(OSError):
					self.take()
				manifest = snapshot.read_manifest()
				self.assertEqual(manifest["taken_at"], first["taken_at"])
				self.assertEqual(self.listing(), sorted([first["generation"], snapshot.POINTER]))
				self.loaded.clear()
				snapshot.restore_snapshot(manifest)
				self.assertEqual(
					self.loaded, [os.path.join(self.root, first["generation"], snapshot.DATABASE)]
				)

	def test_leftovers_of_a_killed_snapshot_are_removed(self):
		snapshot = self.snapshot
		first = self.take()
		os.mkdir(os.path.join(self.root, "g-20260101T000000-deadbeef"))
		for name in ("current.partial", ".database.sql.gz.partial"):
			open(os.path.join(self.root, name), "w").close()
		self.assertEqual(snapshot.read_manifest()["taken_at"], first["taken_at"])

		def leftovers_already_gone():
			# Cleared before the dump, so a dead run's dump and the new one never share the disk.
			for name in ("g-20260101T000000-deadbeef", "current.partial", ".database.sql.gz.partial"):
				self.assertNotIn(name, self.listing())

		self.before_dump = leftovers_already_gone
		second = self.take()
		self.assertEqual(self.listing(), sorted([second["generation"], snapshot.POINTER]))

	def test_a_switched_generation_is_used_before_cleanup(self):
		snapshot = self.snapshot
		first = self.take()
		with patch.object(snapshot, "_remove_unused"):
			second = self.take()
		self.assertEqual(snapshot.read_manifest()["taken_at"], second["taken_at"])
		self.assertIn(first["generation"], self.listing())
		# a manifest read before the switch still restores its own generation
		self.loaded.clear()
		snapshot.restore_snapshot({**first, "generation": first["generation"]})
		self.assertEqual(self.loaded, [os.path.join(self.root, first["generation"], snapshot.DATABASE)])
		third = self.take()
		self.assertEqual(self.listing(), sorted([third["generation"], snapshot.POINTER]))

	def test_flush_failure_after_the_switch_is_not_fatal(self):
		snapshot = self.snapshot
		real_flush = snapshot._flush

		def flush(path):
			if os.path.abspath(path) == os.path.abspath(self.root):
				raise OSError("EIO")
			return real_flush(path)

		first = self.take()
		with patch.object(snapshot, "_flush", side_effect=flush):
			taken = self.take()
		self.assertEqual(snapshot.read_manifest()["generation"], taken["generation"])
		snapshot.restore_snapshot(snapshot.read_manifest())
		# The switch may not be on disk: the generation a crash could fall back to stays...
		self.assertEqual(self.listing(), sorted([first["generation"], taken["generation"], snapshot.POINTER]))
		# ...until the next snapshot.
		third = self.take()
		self.assertEqual(self.listing(), sorted([third["generation"], snapshot.POINTER]))

	def test_damaged_pointer_is_refused(self):
		snapshot = self.snapshot
		kept = self.take()["generation"]
		well_formed = "g-20260101T000000-0badcafe"
		for text in ("", "../../private/files", well_formed, "not a generation"):
			with self.subTest(pointer=text):
				if text == well_formed:
					os.makedirs(os.path.join(self.root, text), exist_ok=True)
				with open(os.path.join(self.root, snapshot.POINTER), "w") as f:
					f.write(text)
				with self.assertRaises(snapshot.SnapshotError):
					snapshot.read_manifest()
				with self.assertRaises(snapshot.SnapshotError):
					self.take()
				self.assertIn(kept, self.listing())
		with self.assertRaises(snapshot.SnapshotError):
			snapshot.manifest_folder({"generation": "../files"})

	def test_damaged_file_is_refused_before_loading(self):
		snapshot = self.snapshot
		taken = self.take()
		with open(os.path.join(self.root, taken["generation"], "public-files.tar.gz"), "ab") as f:
			f.write(b"x")
		with self.assertRaises(snapshot.SnapshotError):
			snapshot.restore_snapshot(snapshot.read_manifest())
		manifest = snapshot.read_manifest()
		manifest["files"].pop(snapshot.DATABASE)
		with self.assertRaises(snapshot.SnapshotError):
			snapshot.restore_snapshot(manifest)
		self.assertEqual(self.loaded, [])

	def test_flat_snapshot_from_earlier_releases_still_restores(self):
		import json

		snapshot = self.snapshot
		files = {}
		for name in snapshot.SNAPSHOT_FILES:
			path = os.path.join(self.root, name)
			with open(path, "wb") as f:
				f.write(name.encode())
			files[name] = {"bytes": len(name), "sha256": snapshot._sha256(path)}
		with open(os.path.join(self.root, snapshot.MANIFEST), "w") as f:
			json.dump({"version": 1, "taken_at": "2026-10-15 23:00:00", "files": files}, f)

		manifest = snapshot.read_manifest()
		self.assertIsNone(manifest["generation"])
		snapshot.restore_snapshot(manifest)
		self.assertEqual(self.loaded, [os.path.join(self.root, snapshot.DATABASE)])

		with (
			patch.object(snapshot, "app_versions", side_effect=OSError("versions")),
			self.assertRaises(OSError),
		):
			self.take()
		self.assertEqual(self.listing(), sorted((snapshot.MANIFEST, *snapshot.SNAPSHOT_FILES)))
		taken = self.take()
		self.assertEqual(self.listing(), sorted([taken["generation"], snapshot.POINTER]))

	def test_permissions_and_names(self):
		import stat

		snapshot = self.snapshot
		# A folder left with another mode (here not even writable): take_snapshot sets 0700 itself.
		os.chmod(self.root, 0o500)
		name = self.take()["generation"]
		self.assertRegex(name, snapshot.GENERATION)
		mode = lambda path: stat.S_IMODE(os.stat(path).st_mode)  # noqa: E731
		self.assertEqual(mode(self.root), 0o700)
		self.assertEqual(mode(os.path.join(self.root, name)), 0o700)
		self.assertEqual(mode(os.path.join(self.root, snapshot.POINTER)), 0o600)
		for file_name in os.listdir(os.path.join(self.root, name)):
			self.assertEqual(mode(os.path.join(self.root, name, file_name)), 0o600, file_name)


MANIFEST = {"taken_at": "2026-10-15 23:00:00", "generation": "g-20261015T230000-00000000", "apps": {}}


class TestResetFlow(IntegrationTestCase):
	"""The order of a reset and of a repeated preparation, and what each failure leaves behind."""

	def flow_mocks(self, extra=None, **failures):
		from unittest.mock import MagicMock

		from hostel360.demo import site, snapshot

		manager = MagicMock()
		result = {"days": 1, "rows_shifted": 0, "created": {}, "errors": [], "migrated": False}
		mocks = {
			(snapshot, "read_manifest"): {"return_value": dict(MANIFEST)},
			(snapshot, "verify_snapshot"): {},
			(snapshot, "restore_snapshot"): {},
			(site, "set_config"): {},
			(site, "site_config_value"): {"return_value": None},
			(site, "drain_background_jobs"): {},
			(site, "site_jobs"): {"return_value": ([], [])},
			(site, "drop_queued_jobs"): {},
			(site, "clear_document_locks"): {},
			(site, "bring_to_today"): {"return_value": result},
			(site, "snapshot"): {"return_value": {"taken_at": "2026-10-16 23:00:00", "generation": "g"}},
			**(extra or {}),
		}
		for (module, name), options in mocks.items():
			options = {**options, **failures.get(name, {})}
			mock = self.enterContext(patch.object(module, name, **options))
			manager.attach_mock(mock, name)
		self.enterContext(patch.object(frappe, "clear_cache"))
		return manager

	def run_reset(self, **failures):
		from hostel360.demo import site

		manager = self.flow_mocks(**failures)
		with demo_conf():
			try:
				site.reset_demo_site()
			except BaseException as e:
				return manager, e
		return manager, None

	def flow(self, manager):
		"""The calls in order, set_config calls with what they set."""
		return [
			(name, args[0]) if name == "set_config" else name
			for name, args, _kwargs in manager.mock_calls
			if "." not in name and name != "site_config_value"
		]

	def config_calls(self, manager):
		return [c.args[0] for c in manager.set_config.call_args_list]

	def test_reset_order(self):
		from hostel360.demo import RESTORING_KEY

		manager, error = self.run_reset()
		self.assertIsNone(error)
		# The job guard goes up only after the grace and comes down only after anything queued
		# during the load was dropped and the old locks cleared.
		self.assertEqual(
			self.flow(manager),
			[
				"read_manifest",
				"verify_snapshot",
				("set_config", {"maintenance_mode": 1}),
				("set_config", {RESTORING_KEY: 1}),
				"drain_background_jobs",
				"restore_snapshot",
				"drop_queued_jobs",
				"clear_document_locks",
				("set_config", {RESTORING_KEY: None}),
				"bring_to_today",
				("set_config", {"maintenance_mode": None, RESTORING_KEY: None}),
			],
		)

	def test_jobs_that_will_not_stop_leave_the_site_open_and_untouched(self):
		from hostel360.demo import RESTORING_KEY
		from hostel360.demo.site import DemoSiteError

		manager, error = self.run_reset(drain_background_jobs={"side_effect": DemoSiteError("busy")})
		self.assertIsInstance(error, DemoSiteError)
		manager.restore_snapshot.assert_not_called()
		# no job of the site runs any more, so the locks of those it refused or dropped go too
		manager.clear_document_locks.assert_called_once()
		self.assertEqual(self.config_calls(manager)[-1], {"maintenance_mode": None, RESTORING_KEY: None})

	def test_locks_of_a_job_that_still_runs_are_kept(self):
		from hostel360.demo.site import DemoSiteError

		manager, error = self.run_reset(
			drain_background_jobs={"side_effect": DemoSiteError("busy")},
			site_jobs={"return_value": ([], ["s||a"])},
		)
		self.assertIsInstance(error, DemoSiteError)
		manager.clear_document_locks.assert_not_called()

	def test_an_aborted_close_keeps_a_site_an_earlier_failure_closed(self):
		# A failed load left the site closed over half a restore; a second reset that is
		# interrupted before it restores anything must not open it.
		manager, error = self.run_reset(
			site_config_value={"return_value": 1}, drain_background_jobs={"side_effect": KeyboardInterrupt}
		)
		self.assertIsInstance(error, KeyboardInterrupt)
		self.assertNotIn(None, [c.get("maintenance_mode", 1) for c in self.config_calls(manager)])
		manager.clear_document_locks.assert_not_called()

	def test_failed_restore_keeps_the_site_closed(self):
		manager, error = self.run_reset(restore_snapshot={"side_effect": OSError("load")})
		self.assertIsInstance(error, OSError)
		self.assertNotIn(None, [c.get("maintenance_mode", 1) for c in self.config_calls(manager)])

	def test_damaged_snapshot_leaves_the_demo_running(self):
		from hostel360.demo.snapshot import SnapshotError

		manager, error = self.run_reset(verify_snapshot={"side_effect": SnapshotError("damaged")})
		self.assertIsInstance(error, SnapshotError)
		manager.set_config.assert_not_called()

	def test_failed_snapshot_after_a_migrate_opens_the_site(self):
		migrated = {"days": 1, "rows_shifted": 0, "created": {}, "errors": [], "migrated": True}
		for failure in (OSError("disk"), KeyboardInterrupt()):
			with self.subTest(failure=type(failure).__name__):
				manager, error = self.run_reset(
					bring_to_today={"return_value": migrated}, snapshot={"side_effect": failure}
				)
				self.assertIs(type(error), type(failure))
				self.assertEqual(self.config_calls(manager)[-1].get("maintenance_mode", "unset"), None)

	def run_prepare(self, live=True, origin="new site", snapshot_taken=None, **failures):
		from hostel360.demo import site

		extra = {
			(site, name): options
			for name, options in {
				"run_site_setup": {},
				"apply_safe_settings": {},
				"ensure_staff_accounts": {"return_value": []},
				"ensure_portal_accounts": {"return_value": []},
				"assign_work": {},
				"sign_everyone_out": {},
				"new_password": {"return_value": "Lantern-Lotus-1234"},
			}.items()
		}
		manager = self.flow_mocks(extra, **failures)
		generate = self.enterContext(
			patch(
				"hostel360.hostel_migration.demo_data.generate",
				return_value={},
				**failures.get("generate", {}),
			)
		)
		manager.attach_mock(generate, "generate")
		self.enterContext(patch.object(frappe.db, "commit"))
		values = {"hostel360_demo_origin": origin, site.SNAPSHOT_TAKEN_KEY: snapshot_taken}
		with demo_conf(on=live, **values):
			try:
				site.prepare_demo_site()
			except BaseException as e:
				return manager, e
		return manager, None

	def test_prepare_on_a_live_demo_closes_restores_and_reopens(self):
		from hostel360.demo import RESTORING_KEY

		manager, error = self.run_prepare()
		self.assertIsNone(error)
		steps = [
			"read_manifest",
			"verify_snapshot",
			("set_config", {"maintenance_mode": 1}),
			("set_config", {RESTORING_KEY: 1}),
			"drain_background_jobs",
			"restore_snapshot",
			"drop_queued_jobs",
			"clear_document_locks",
			("set_config", {RESTORING_KEY: None}),
			"bring_to_today",
			"run_site_setup",
			"generate",
			"snapshot",
			("set_config", {"maintenance_mode": None, RESTORING_KEY: None}),
		]
		self.assertEqual([call for call in self.flow(manager) if call in steps], steps)

	def test_prepare_failing_after_the_restore_keeps_the_site_closed(self):
		from hostel360.demo.site import DemoSiteError

		manager, error = self.run_prepare(run_site_setup={"side_effect": DemoSiteError("setup")})
		self.assertIsInstance(error, DemoSiteError)
		self.assertNotIn(None, [c.get("maintenance_mode", 1) for c in self.config_calls(manager)])

	def test_prepare_with_busy_jobs_restores_nothing(self):
		from hostel360.demo.site import DemoSiteError

		manager, error = self.run_prepare(drain_background_jobs={"side_effect": DemoSiteError("busy")})
		self.assertIsInstance(error, DemoSiteError)
		manager.restore_snapshot.assert_not_called()
		manager.run_site_setup.assert_not_called()
		self.assertIsNone(self.config_calls(manager)[-1].get("maintenance_mode", "unset"))

	def test_a_lost_snapshot_is_never_replaced_by_the_visited_site(self):
		from hostel360.demo.site import DemoSiteError

		manager, error = self.run_prepare(
			read_manifest={"return_value": None}, snapshot_taken="2026-10-15 23:00:00"
		)
		self.assertIsInstance(error, DemoSiteError)
		manager.set_config.assert_not_called()
		manager.snapshot.assert_not_called()

	def test_an_earlier_release_demo_is_recorded_before_its_snapshot_is_checked(self):
		from hostel360.demo import ORIGIN_KEY
		from hostel360.demo.site import ORIGIN_EARLIER_RELEASE, SNAPSHOT_TAKEN_KEY
		from hostel360.demo.snapshot import SnapshotError

		manager, error = self.run_prepare(
			origin=None, verify_snapshot={"side_effect": SnapshotError("damaged")}
		)
		self.assertIsInstance(error, SnapshotError)
		self.assertEqual(
			self.config_calls(manager),
			[{ORIGIN_KEY: ORIGIN_EARLIER_RELEASE, SNAPSHOT_TAKEN_KEY: MANIFEST["taken_at"]}],
		)

	def test_prepare_refuses_a_damaged_snapshot_before_changing_anything(self):
		from hostel360.demo import site, snapshot

		with (
			patch.object(snapshot, "read_manifest", side_effect=snapshot.SnapshotError("damaged")),
			patch.object(site, "set_config") as set_config,
			patch.object(site, "run_site_setup") as setup,
			self.assertRaises(snapshot.SnapshotError),
		):
			site.prepare_demo_site()
		set_config.assert_not_called()
		setup.assert_not_called()


class TestJobDrain(IntegrationTestCase):
	def test_guard_refuses_only_while_restoring(self):
		from hostel360.demo import RESTORING_KEY, refuse_jobs_while_restoring

		with demo_conf(**{RESTORING_KEY: 1, "maintenance_mode": 1}), self.assertRaises(frappe.InReadOnlyMode):
			refuse_jobs_while_restoring(method="x")
		for values in (
			{RESTORING_KEY: 1, "maintenance_mode": 0},
			{RESTORING_KEY: 0, "maintenance_mode": 1},
			{},
		):
			with self.subTest(values=values), demo_conf(**values):
				self.assertIsNone(refuse_jobs_while_restoring(method="x"))

	def test_guard_refusal_writes_nothing(self):
		# The guard depends on Frappe calling before_job hooks outside the job's error handling,
		# which would otherwise log an Error Log row and commit it into the tables being loaded.
		from frappe.utils.background_jobs import execute_job

		from hostel360.demo import RESTORING_KEY

		errors = frappe.db.count("Error Log")
		self.addCleanup(forget_local_job)
		with demo_conf(**{RESTORING_KEY: 1, "maintenance_mode": 1}), self.assertRaises(frappe.InReadOnlyMode):
			execute_job(
				frappe.local.site, "hostel360.demo.test_demo._job_must_not_run", None, "x", {}, is_async=False
			)
		self.assertEqual(frappe.db.count("Error Log"), errors)

	def fake_queues(self, queued, started, removable=None):
		from unittest.mock import MagicMock

		queue = MagicMock()
		queue.get_job_ids.return_value = list(queued)
		queue.started_job_registry.get_job_ids.return_value = list(started)
		queue.remove.side_effect = lambda job_id: 0 if job_id in (removable or ()) else 1
		return (
			patch("frappe.utils.background_jobs.get_queue_list", return_value=["default"]),
			patch("frappe.utils.background_jobs.get_queue", return_value=queue),
			queue,
		)

	def test_jobs_are_matched_to_the_site(self):
		from hostel360.demo.site import site_jobs

		lists, get, _queue = self.fake_queues(["s||a", "sx||b", "other||c"], ["s||d", "other||e"])
		with lists, get:
			self.assertEqual(site_jobs("s"), (["s||a"], ["s||d"]))

	def test_only_this_sites_waiting_jobs_are_dropped(self):
		from hostel360.demo.site import drop_queued_jobs

		lists, get, queue = self.fake_queues(["s||a", "s||b", "other||c"], [], removable=["s||b"])
		with lists, get, patch("rq.job.Job.fetch") as fetch:
			self.assertEqual(drop_queued_jobs("s"), 1)
		self.assertEqual([c.args[0] for c in queue.remove.call_args_list], ["s||a", "s||b"])
		fetch.assert_called_once()
		self.assertEqual(fetch.call_args.args[0], "s||a")

	def drain(self, running_rounds, clock, stop_side_effect=None):
		from hostel360.demo import site

		rounds = iter(running_rounds)
		with (
			patch.object(site, "drop_queued_jobs"),
			patch.object(site, "site_jobs", side_effect=lambda s: ([], next(rounds, running_rounds[-1]))),
			patch.object(site, "_clock", side_effect=clock),
			patch.object(site, "_sleep") as sleep,
			patch("frappe.utils.background_jobs.get_redis_conn"),
			patch("rq.command.send_stop_job_command", side_effect=stop_side_effect) as stop,
		):
			try:
				site.drain_background_jobs("s", lambda message: None)
			except site.DemoSiteError as e:
				return sleep, stop, e
		return sleep, stop, None

	def test_a_quiet_site_is_not_waited_for(self):
		sleep, stop, error = self.drain([[]], iter([0, 0]))
		self.assertIsNone(error)
		sleep.assert_not_called()
		stop.assert_not_called()

	def test_running_jobs_are_waited_for(self):
		sleep, stop, error = self.drain([["s||a"], ["s||a"], []], iter([0, 3, 6, 9]))
		self.assertIsNone(error)
		self.assertEqual(sleep.call_count, 2)
		stop.assert_not_called()

	def test_jobs_are_stopped_once_after_the_wait(self):
		from rq.exceptions import InvalidJobOperation

		from hostel360.demo.site import JOB_WAIT_SECONDS

		clock = iter([0, JOB_WAIT_SECONDS, JOB_WAIT_SECONDS + 3, JOB_WAIT_SECONDS + 6, JOB_WAIT_SECONDS + 9])
		_sleep, stop, error = self.drain(
			[["s||a"], ["s||a"], ["s||a"], []], clock, stop_side_effect=[InvalidJobOperation("not yet"), None]
		)
		self.assertIsNone(error)
		self.assertEqual([c.args[1] for c in stop.call_args_list], ["s||a", "s||a"])

	def test_a_job_that_will_not_stop_aborts_the_reset(self):
		from hostel360.demo.site import JOB_STOP_SECONDS, JOB_WAIT_SECONDS

		end = JOB_WAIT_SECONDS + JOB_STOP_SECONDS
		_sleep, _stop, error = self.drain([["s||a"]], iter([0, JOB_WAIT_SECONDS, end]))
		self.assertIsNotNone(error)
		self.assertIn("s||a", str(error))

	def test_only_document_locks_are_cleared(self):
		import tempfile

		from hostel360.demo.site import clear_document_locks

		with tempfile.TemporaryDirectory() as root:
			locks = os.path.join(root, "locks")
			os.makedirs(locks)
			names = ["a" * 56 + ".lock", "hostel360_demo.lock", "bench_migrate.lock"]
			for name in names:
				open(os.path.join(locks, name), "w").close()
			with patch.object(frappe, "get_site_path", side_effect=lambda *parts: os.path.join(root, *parts)):
				clear_document_locks()
			self.assertEqual(sorted(os.listdir(locks)), sorted(names[1:]))


class TestDemoSiteSafety(IntegrationTestCase):
	def test_refuses_sites_with_real_records(self):
		from hostel360.demo.site import DemoSiteError, check_site_origin, records_a_new_site_does_not_have
		from hostel360.hostel_people.testing import make_student

		make_student()
		self.assertIn("Hostel Student", dict(records_a_new_site_does_not_have()))
		with demo_conf(on=False), self.assertRaises(DemoSiteError):
			check_site_origin(None)

	def test_reset_refused_unless_demo(self):
		from hostel360.demo.site import DemoSiteError, reset_demo_site

		with demo_conf(on=False), self.assertRaises(DemoSiteError):
			reset_demo_site()

	def test_reset_needs_a_snapshot(self):
		from hostel360.demo import snapshot
		from hostel360.demo.site import DemoSiteError, reset_demo_site

		with demo_conf(), patch.object(snapshot, "read_manifest", return_value=None):
			with self.assertRaises(DemoSiteError):
				reset_demo_site()

	def test_demo_password_passes_the_policy(self):
		from frappe.utils.password_strength import test_password_strength

		from hostel360.demo.site import new_password

		passwords = {new_password() for _i in range(10)}
		self.assertGreater(len(passwords), 1)
		for password in passwords:
			self.assertGreaterEqual(test_password_strength(password).get("score", 0), 3)


def tearDownModule():
	for mobile in ("9876543210",):
		clear_otp_keys(mobile, TEST_IP)
