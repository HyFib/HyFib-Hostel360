# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, set_user
from frappe.utils import add_days, getdate, nowdate

from hostel360.hostel_mess.doctype.mess_menu.mess_menu import get_previous_week_items
from hostel360.hostel_mess.doctype.mess_stock_entry.mess_stock_entry import get_item_details
from hostel360.hostel_mess.permissions import (
	MESS_SCOPED_DOCTYPES,
	assert_doc_mess_access,
	get_permitted_messes,
	mess_has_permission,
	mess_query_conditions,
)
from hostel360.hostel_mess.services import cancel_mess_reduction, get_mess_for_student
from hostel360.hostel_mess.tasks import send_low_stock_digest
from hostel360.hostel_mess.testing import (
	EXTERNAL_DEPENDENCIES,
	make_hostel,
	make_item,
	make_mess,
	make_reduction,
	make_stock_entry,
	make_student,
	make_user,
	set_mess_settings,
	unique,
)
from hostel360.hostel_mess.utils import get_week_start
from hostel360.setup.roles import (
	AUDITOR_ROLE,
	DEPUTY_WARDEN_ROLE,
	MESS_MANAGER_ROLE,
	MESS_STAFF_ROLE,
	OFFICE_ROLE,
)

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


class TestHostelMess(IntegrationTestCase):
	def test_hostel_is_served_by_one_active_mess(self):
		hostel = make_hostel()
		mess = make_mess([hostel])
		with self.assertRaisesRegex(frappe.ValidationError, "already served"):
			make_mess([hostel])

		frappe.db.set_value("Hostel Mess", mess, "is_active", 0)
		replacement = make_mess([hostel])
		self.assertEqual(get_mess_for_student(make_student(hostel)), replacement)

	def test_manager_needs_mess_manager_role(self):
		with self.assertRaisesRegex(frappe.ValidationError, "role"):
			make_mess([make_hostel()], manager=make_user([MESS_STAFF_ROLE]))
		self.assertTrue(make_mess([make_hostel()], manager=make_user([MESS_MANAGER_ROLE])))

	def test_duplicate_hostel_rows_rejected(self):
		hostel = make_hostel()
		with self.assertRaisesRegex(frappe.ValidationError, "more than once"):
			frappe.get_doc(
				{
					"doctype": "Hostel Mess",
					"mess_name": unique("Mess"),
					"hostels": [{"hostel": hostel}, {"hostel": hostel}],
				}
			).insert(ignore_permissions=True)

	def test_weekly_low_stock_digest(self):
		set_mess_settings(low_stock_alert=1)
		manager = make_user([MESS_MANAGER_ROLE])
		mess = make_mess([make_hostel()], manager=manager)
		dal = make_item("Kg", reorder_level=50)
		rice = make_item("Kg", reorder_level=10)
		make_stock_entry(mess, "Receipt", [(dal, 20, 110), (rice, 100, 40)], add_days(nowdate(), -1))

		with (
			patch("hostel360.hostel_mess.tasks.send_notification") as notify,
			patch("hostel360.hostel_mess.tasks.user_recipients", return_value=[{"user": manager}]) as rcpt,
		):
			send_low_stock_digest()

		calls = [c for c in notify.call_args_list if c.kwargs.get("reference_name") == mess]
		self.assertEqual(len(calls), 1)
		event, recipients, context = calls[0].args[:3]
		self.assertEqual(event, "mess_low_stock")
		self.assertEqual(recipients, [{"user": manager}])
		self.assertIn(([manager],), [c.args for c in rcpt.call_args_list])
		# Only items this mess has stocked are reported.
		self.assertEqual([row["item"] for row in context["items"]], [dal])
		self.assertEqual(context["item_count"], 1)
		self.assertIn(dal, context["items_summary"])

		set_mess_settings(low_stock_alert=0)
		with patch("hostel360.hostel_mess.tasks.send_notification") as notify:
			send_low_stock_digest()
		notify.assert_not_called()


def restrict_to_hostel(user: str, hostel: str) -> None:
	"""What Hostel Staff writes for hostel-scoped designations."""
	frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": user,
			"allow": "Hostel",
			"for_value": hostel,
			"apply_to_all_doctypes": 1,
		}
	).insert(ignore_permissions=True)


class TestMessScoping(IntegrationTestCase):
	"""Hostel-scoped staff work only with the messes that serve their hostels."""

	def setUp(self):
		self.boys_hostel, self.girls_hostel = make_hostel(), make_hostel(gender="Female")
		self.boys_mess = make_mess([self.boys_hostel])
		self.girls_mess = make_mess([self.girls_hostel])
		self.staff = make_user([MESS_STAFF_ROLE])
		restrict_to_hostel(self.staff, self.boys_hostel)
		self.monday = get_week_start(add_days(nowdate(), 7))

	def menu(self, mess):
		return frappe.get_doc(
			{
				"doctype": "Mess Menu",
				"mess": mess,
				"week_start_date": self.monday,
				"items": [{"menu_date": self.monday, "meal": "Lunch", "dishes": "Rice, sambar"}],
			}
		)

	def test_permitted_messes_follow_hostel_permissions(self):
		self.assertEqual(get_permitted_messes(self.staff), {self.boys_mess})
		self.assertIsNone(get_permitted_messes(make_user([MESS_MANAGER_ROLE])))
		self.assertIsNone(get_permitted_messes("Administrator"))

		condition = mess_query_conditions(self.staff, "Mess Menu")
		self.assertIn(frappe.db.escape(self.boys_mess), condition)
		self.assertNotIn(frappe.db.escape(self.girls_mess), condition)
		self.assertIn("`tabHostel Mess`.`name`", mess_query_conditions(self.staff, "Hostel Mess"))
		self.assertEqual(mess_query_conditions(self.staff, "Mess Settings"), "")
		self.assertTrue(set(MESS_SCOPED_DOCTYPES) >= {"Mess Menu", "Mess Stock Entry", "Mess Bill Run"})

		girls_menu = self.menu(self.girls_mess).insert(ignore_permissions=True)
		boys_menu = self.menu(self.boys_mess).insert(ignore_permissions=True)
		self.assertFalse(mess_has_permission(girls_menu, "write", self.staff))
		self.assertTrue(mess_has_permission(boys_menu, "write", self.staff))
		# A request cannot move a stored record into the user's mess to reach it.
		girls_menu.mess = self.boys_mess
		self.assertFalse(mess_has_permission(girls_menu, "write", self.staff))

	def test_scoped_staff_cannot_write_another_mess(self):
		item = make_item("Kg")
		make_stock_entry(self.girls_mess, "Receipt", [(item, 10, 50)], add_days(nowdate(), -1))
		girls_menu = self.menu(self.girls_mess).insert(ignore_permissions=True)
		with set_user(self.staff):
			with self.assertRaises(frappe.PermissionError):
				self.menu(self.girls_mess).update({"week_start_date": add_days(self.monday, 7)}).insert()
			with self.assertRaises(frappe.PermissionError):
				girls_menu.publish()
			with self.assertRaises(frappe.PermissionError):
				get_previous_week_items(self.girls_mess, str(add_days(self.monday, 7)))
			with self.assertRaises(frappe.PermissionError):
				get_item_details(self.girls_mess, item)
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc(
					{
						"doctype": "Mess Stock Entry",
						"entry_type": "Issue",
						"mess": self.girls_mess,
						"posting_date": nowdate(),
						"items": [{"item": item, "qty": 1}],
					}
				).insert()
			# Their own mess is unaffected.
			own = self.menu(self.boys_mess).insert()
			own.publish()
			self.assertEqual(own.status, "Published")

	def test_permission_hooks_scope_lists_and_documents(self):
		"""With the hooks registered as requested for hooks.py, Frappe applies the rule itself."""
		real_get_hooks = frappe.get_hooks
		module = "hostel360.hostel_mess.permissions"

		def get_hooks(hook=None, default="_KEEP_DEFAULT_LIST", app_name=None):
			hooks = real_get_hooks(hook, default, app_name)
			if hook == "permission_query_conditions":
				return {
					**(hooks or {}),
					**{dt: [f"{module}.mess_query_conditions"] for dt in MESS_SCOPED_DOCTYPES},
				}
			if hook == "has_permission":
				return {
					**(hooks or {}),
					**{dt: [f"{module}.mess_has_permission"] for dt in MESS_SCOPED_DOCTYPES},
				}
			return hooks

		girls_menu = self.menu(self.girls_mess).insert(ignore_permissions=True)
		boys_menu = self.menu(self.boys_mess).insert(ignore_permissions=True)
		with patch("frappe.get_hooks", side_effect=get_hooks), set_user(self.staff):
			self.assertEqual(set(frappe.get_list("Hostel Mess", pluck="name")), {self.boys_mess})
			menus = set(frappe.get_list("Mess Menu", filters={"week_start_date": self.monday}, pluck="name"))
			self.assertEqual(menus, {boys_menu.name})
			self.assertFalse(frappe.has_permission("Mess Menu", "read", girls_menu.name))
			self.assertTrue(frappe.has_permission("Mess Menu", "write", boys_menu.name))

	def test_unrestricted_staff_are_not_limited(self):
		manager = make_user([MESS_MANAGER_ROLE])
		with set_user(manager):
			menu = self.menu(self.girls_mess).insert()
		self.assertEqual(menu.mess, self.girls_mess)

	def test_student_records_survive_a_mess_reorganisation(self):
		"""A hostel moved to a new mess keeps seeing its students' earlier bills, extras and reductions."""
		moved = make_hostel()
		shared = frappe.get_doc("Hostel Mess", self.boys_mess)
		shared.append("hostels", {"hostel": moved})
		shared.save(ignore_permissions=True)

		student = make_student(moved)
		absence = (add_days(nowdate(), 10), add_days(nowdate(), 14))
		reduction = make_reduction(student, *absence, status="Pending")
		extra = frappe.get_doc(
			{
				"doctype": "Mess Extra Charge",
				"student": student,
				"charge_date": nowdate(),
				"charge_type": "Guest Meal",
				"description": "Parent stayed for lunch",
				"amount": 120,
			}
		).insert(ignore_permissions=True)
		bill = frappe.get_doc(
			{
				"doctype": "Mess Bill",
				"mess_bill_run": f"{self.boys_mess}-2026-09-01",
				"mess": self.boys_mess,
				"student": student,
				"hostel": moved,
				"from_date": "2026-09-01",
				"to_date": "2026-09-30",
				"status": "Published",
				"total_amount": 900,
			}
		)
		bill.flags.from_bill_run = True
		bill.flags.ignore_links = True
		bill.insert(ignore_permissions=True)
		records = {
			"Mess Reduction Application": reduction,
			"Mess Extra Charge": extra.name,
			"Mess Bill": bill.name,
		}
		for doctype, name in records.items():
			self.assertEqual(frappe.db.get_value(doctype, name, ["mess", "hostel"]), (self.boys_mess, moved))
		old_menu = self.menu(self.boys_mess).insert(ignore_permissions=True)

		deputy, office = make_user([DEPUTY_WARDEN_ROLE]), make_user([OFFICE_ROLE])
		for user in (deputy, office):
			restrict_to_hostel(user, moved)

		# The moved hostel gets a mess of its own; the shared one goes on serving the boys' hostel.
		shared.reload()
		shared.set("hostels", [row for row in shared.hostels if row.hostel != moved])
		shared.save(ignore_permissions=True)
		new_mess = make_mess([moved])

		for user in (deputy, office):
			self.assertEqual(get_permitted_messes(user), {new_mess})
			with set_user(user):
				for doctype, name in records.items():
					listed = frappe.get_list(doctype, filters={"student": student}, pluck="name")
					self.assertEqual(listed, [name], f"{doctype} for {user}")
					self.assertTrue(frappe.has_permission(doctype, "read", name), doctype)
				# Records without a hostel stay with the messes that serve the user's hostels.
				menus = frappe.get_list("Mess Menu", filters={"mess": self.boys_mess}, pluck="name")
				self.assertNotIn(old_menu.name, menus)
				self.assertFalse(frappe.has_permission("Mess Menu", "read", old_menu.name))

		# Keeping the old mess is fine; filing into it or moving to another mess is not.
		stored = frappe.get_doc("Mess Extra Charge", extra.name)
		self.assertTrue(mess_has_permission(stored, "write", deputy))
		stored.mess = self.girls_mess
		self.assertFalse(mess_has_permission(stored, "write", deputy))
		stored.mess = new_mess
		self.assertTrue(mess_has_permission(stored, "write", deputy))
		fresh = frappe.get_doc({"doctype": "Mess Extra Charge", "student": student, "mess": self.boys_mess})
		self.assertFalse(mess_has_permission(fresh, "create", deputy))
		with set_user(deputy), self.assertRaises(frappe.PermissionError):
			assert_doc_mess_access(fresh)
		with set_user(deputy):
			assert_doc_mess_access(frappe.get_doc("Mess Extra Charge", extra.name))

		# Staff of the hostel the old mess still serves do not pick up the moved hostel's records.
		boys_deputy = make_user([DEPUTY_WARDEN_ROLE])
		restrict_to_hostel(boys_deputy, self.boys_hostel)
		with set_user(boys_deputy):
			for doctype in records:
				self.assertEqual(frappe.get_list(doctype, filters={"student": student}, pluck="name"), [])

		cancel_mess_reduction(reduction, deputy)
		self.assertEqual(frappe.db.get_value("Mess Reduction Application", reduction, "status"), "Cancelled")

	def test_hostel_records_scoped_by_hostel_when_no_mess_serves_it(self):
		hostel = make_hostel()
		user = make_user([OFFICE_ROLE])
		restrict_to_hostel(user, hostel)
		self.assertEqual(mess_query_conditions(user, "Mess Menu"), "1 = 0")
		condition = mess_query_conditions(user, "Mess Bill")
		self.assertEqual(condition, f"`tabMess Bill`.`hostel` in ({frappe.db.escape(hostel)})")

		condition = mess_query_conditions(self.staff, "Mess Reduction Application")
		table = "`tabMess Reduction Application`"
		self.assertIn(f"{table}.`mess` in ({frappe.db.escape(self.boys_mess)})", condition)
		self.assertIn(f"{table}.`hostel` in ({frappe.db.escape(self.boys_hostel)})", condition)
		self.assertNotIn("`hostel`", mess_query_conditions(self.staff, "Mess Bill Run"))


class TestMessPermissionRules(IntegrationTestCase):
	"""DocPerm and report role rules from the design's permission matrix."""

	MESS_DOCTYPES = (
		"Hostel Mess",
		"Mess Bill",
		"Mess Bill Run",
		"Mess Expense",
		"Mess Extra Charge",
		"Mess Indent",
		"Mess Item",
		"Mess Menu",
		"Mess Reduction Application",
		"Mess Stock Entry",
		"Mess Stock Ledger Entry",
		"Mess Supplier",
	)

	def test_read_only_roles_cannot_share(self):
		auditor = make_user([AUDITOR_ROLE])
		deputy = make_user([DEPUTY_WARDEN_ROLE])
		for doctype in self.MESS_DOCTYPES:
			self.assertTrue(frappe.has_permission(doctype, "read", user=auditor), doctype)
			for ptype in ("share", "email"):
				self.assertFalse(frappe.has_permission(doctype, ptype, user=auditor), f"{doctype} {ptype}")
			self.assertFalse(frappe.has_permission(doctype, "share", user=deputy), doctype)

	def test_mess_staff_have_no_stock_reports(self):
		# The matrix gives Mess Staff no report rights; stock on hand is shown on the entry itself.
		for report in ("Mess Stock Balance", "Mess Consumption"):
			roles = {row.role for row in frappe.get_doc("Report", report).roles}
			self.assertNotIn(MESS_STAFF_ROLE, roles, report)
			self.assertIn(MESS_MANAGER_ROLE, roles, report)

	def test_billed_upto_survives_an_old_form(self):
		mess = frappe.get_doc("Hostel Mess", make_mess([make_hostel()]))
		# Written by a bill run while the form was open.
		frappe.db.set_value("Hostel Mess", mess.name, "billed_upto", "2026-09-30", update_modified=False)
		mess.description = "Kitchen near block A"
		mess.save()
		self.assertEqual(
			getdate(frappe.db.get_value("Hostel Mess", mess.name, "billed_upto")), getdate("2026-09-30")
		)
