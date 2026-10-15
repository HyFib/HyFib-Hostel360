# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from hostel360.hostel_mess.services import get_mess_for_student
from hostel360.hostel_mess.tasks import send_low_stock_digest
from hostel360.hostel_mess.testing import (
	EXTERNAL_DEPENDENCIES,
	make_hostel,
	make_item,
	make_mess,
	make_stock_entry,
	make_student,
	make_user,
	set_mess_settings,
	unique,
)
from hostel360.setup.roles import MESS_MANAGER_ROLE, MESS_STAFF_ROLE

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
