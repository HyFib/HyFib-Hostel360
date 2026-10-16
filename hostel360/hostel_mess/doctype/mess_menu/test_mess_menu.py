# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from hostel360.hostel_mess.doctype.mess_menu.mess_menu import get_previous_week_items
from hostel360.hostel_mess.services import get_today_menu, get_week_menu
from hostel360.hostel_mess.testing import (
	EXTERNAL_DEPENDENCIES,
	make_hostel,
	make_mess,
	make_student,
	make_user,
)
from hostel360.hostel_mess.utils import get_week_start
from hostel360.setup.roles import STUDENT_ROLE

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES


class TestMessMenu(IntegrationTestCase):
	def setUp(self):
		self.hostel = make_hostel()
		self.mess = make_mess([self.hostel])
		self.monday = get_week_start(nowdate())

	def make_menu(self, week_start=None, rows=None, insert=True):
		week_start = getdate(week_start or self.monday)
		rows = rows or [
			(0, "Breakfast", "Idli, sambar, chutney"),
			(0, "Lunch", "Rice, sambar, rasam, poriyal, curd"),
			(0, "Dinner", "Chapathi, kurma"),
			(2, "Dinner", "Veg biryani, raita"),
		]
		doc = frappe.get_doc(
			{
				"doctype": "Mess Menu",
				"mess": self.mess,
				"week_start_date": week_start,
				"items": [
					{"menu_date": add_days(week_start, offset), "meal": meal, "dishes": dishes}
					for offset, meal, dishes in rows
				],
			}
		)
		return doc.insert(ignore_permissions=True) if insert else doc

	def test_week_must_start_on_monday(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Monday"):
			self.make_menu(add_days(self.monday, 2))

	def test_rows_are_cleaned_and_ordered(self):
		menu = self.make_menu(
			rows=[
				(1, "Dinner", "Dosa"),
				(0, "Lunch", "Meals"),
				(0, "Breakfast", "Pongal"),
				(3, "Lunch", "   "),
			]
		)
		self.assertEqual(getdate(menu.week_end_date), getdate(add_days(self.monday, 6)))
		self.assertEqual(
			[(r.day, r.meal) for r in menu.items],
			[("Monday", "Breakfast"), ("Monday", "Lunch"), ("Tuesday", "Dinner")],
		)

		with self.assertRaisesRegex(frappe.ValidationError, "more than once"):
			self.make_menu(add_days(self.monday, 7), rows=[(0, "Lunch", "A"), (0, "Lunch", "B")])
		with self.assertRaisesRegex(frappe.ValidationError, "outside the week"):
			self.make_menu(add_days(self.monday, 14), rows=[(8, "Lunch", "A")])

	def test_one_menu_per_week(self):
		self.make_menu()
		with self.assertRaises(frappe.DuplicateEntryError):
			self.make_menu()

	def test_copy_previous_week(self):
		self.make_menu()
		next_monday = str(add_days(self.monday, 7))
		rows = get_previous_week_items(self.mess, next_monday)
		self.assertEqual(len(rows), 4)
		self.assertEqual(rows[0]["menu_date"], next_monday)
		self.assertEqual(rows[0]["dishes"], "Idli, sambar, chutney")
		self.assertEqual(rows[-1]["day"], "Wednesday")

		with self.assertRaises(frappe.ValidationError):
			get_previous_week_items(self.mess, str(add_days(self.monday, -7)))

	def test_publish_notifies_residents_in_app(self):
		student_user = make_user([STUDENT_ROLE], user_type="Website User")
		make_student(self.hostel, user=student_user)
		menu = self.make_menu()
		with patch("hostel360.hostel_mess.doctype.mess_menu.mess_menu.send_notification") as notify:
			menu.publish()
		self.assertEqual(menu.status, "Published")
		self.assertTrue(menu.published_on)
		event, _recipients, context = notify.call_args.args[:3]
		self.assertEqual(event, "menu_published")
		self.assertEqual(notify.call_args.kwargs["channels"], ["In-App"])
		self.assertEqual(context["week_start"], str(self.monday))
		with self.assertRaises(frappe.ValidationError):
			menu.publish()

	def test_portal_menu_services(self):
		student = make_student(self.hostel)
		menu = self.make_menu(
			rows=[
				(getdate(nowdate()).weekday(), "Dinner", "Parotta, salna"),
				(getdate(nowdate()).weekday(), "Breakfast", "Pongal, vada"),
			]
		)
		# Drafts are not shown to residents.
		self.assertEqual(get_week_menu(self.mess)["days"], [])
		self.assertIsNone(get_today_menu(student))

		with patch("hostel360.hostel_mess.doctype.mess_menu.mess_menu.send_notification"):
			menu.publish()
		week = get_week_menu(self.mess, str(add_days(self.monday, 3)))
		self.assertEqual(week["week_start"], str(self.monday))
		self.assertEqual(len(week["days"]), 7)
		today = get_today_menu(student)
		self.assertEqual([m["meal"] for m in today["meals"]], ["Breakfast", "Dinner"])
		self.assertEqual(today["mess"], self.mess)
