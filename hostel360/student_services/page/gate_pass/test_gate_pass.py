# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import base64

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from hostel360.setup.roles import MESS_MANAGER_ROLE, OFFICE_ROLE, SECURITY_ROLE, WARDEN_ROLE
from hostel360.student_services import services
from hostel360.student_services.page.gate_pass import gate_pass
from hostel360.student_services.testing import (
	capture_notifications,
	leave_data,
	make_block_room,
	make_guardian,
	make_hostel,
	make_scoped_staff,
	make_student,
	make_user,
	put_in_room,
	set_hostel_settings,
	uid,
)
from hostel360.student_services.utils import people_service

# The smallest valid PNG: one transparent pixel.
PIXEL_PNG = base64.b64decode(
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class TestGatePass(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_hostel_settings(leave_requires_parent_approval=0, leave_return_grace_hours=2)
		self.hostel = make_hostel()
		self.warden = make_user([WARDEN_ROLE])
		self.guard = make_scoped_staff("Security", self.hostel)
		self.student = make_student(self.hostel, first_name="Gate" + uid(4).lower())
		self.guardian = make_guardian(self.student.name)

	def tearDown(self):
		frappe.set_user("Administrator")

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def approved_leave(self, student=None, start_in_hours=0.5, hours=48) -> str:
		student = student or self.student
		name = services.apply_leave(
			student.name, leave_data(start_in_hours=start_in_hours, hours=hours), "Student", student.user
		)
		services.warden_decision(name, True, None, self.warden)
		return name

	def board_leaves(self, user, section):
		return {card["leave"]["name"]: card for card in self.as_user(user, gate_pass.get_board)[section]}

	def search(self, user, query):
		return self.as_user(user, gate_pass.search, query)["results"]

	# ------------------------------------------------------------------ board

	def test_board_shows_todays_departures_and_who_is_away(self):
		today = self.approved_leave()
		later = self.approved_leave(make_student(self.hostel), start_in_hours=30)
		elsewhere = self.approved_leave(make_student(make_hostel()))

		leaving = self.board_leaves(self.guard, "leaving")
		self.assertIn(today, leaving)
		self.assertNotIn(later, leaving)
		self.assertNotIn(elsewhere, leaving)
		card = leaving[today]
		self.assertEqual(card["student"], self.student.name)
		self.assertEqual(card["leave"]["action"], "check_out")
		self.assertEqual(self.as_user(self.guard, gate_pass.get_board)["hostels"], [self.hostel])

		self.as_user(self.guard, gate_pass.record_movement, today, "check_out")
		self.assertNotIn(today, self.board_leaves(self.guard, "leaving"))
		away = self.board_leaves(self.guard, "away")
		self.assertEqual(away[today]["leave"]["action"], "return")
		self.assertIn(elsewhere, self.board_leaves(self.warden, "leaving"))

	def test_overdue_students_come_first_on_the_away_list(self):
		on_time = self.approved_leave()
		self.as_user(self.guard, gate_pass.record_movement, on_time, "check_out")
		late_student = make_student(self.hostel)
		late = services.apply_leave(
			late_student.name,
			{
				**leave_data(),
				"from_datetime": add_to_date(now_datetime(), hours=-30),
				"to_datetime": add_to_date(now_datetime(), hours=-5),
			},
			"Warden",
			self.warden,
		)
		services.warden_decision(late, True, None, self.warden)
		frappe.get_doc("Home Visit Leave", late).mark_overdue()

		away = self.as_user(self.guard, gate_pass.get_board)["away"]
		names = [card["leave"]["name"] for card in away]
		self.assertLess(names.index(late), names.index(on_time))
		late_card = away[names.index(late)]["leave"]
		self.assertGreaterEqual(late_card["overdue_hours"], 4.9)
		self.assertEqual(late_card["action"], "return")
		self.assertTrue(late_card["note"])  # never checked out

	# ------------------------------------------------------------------ actions

	def test_check_out_and_return_at_the_gate_tell_the_parents(self):
		leave = self.approved_leave()
		rejected = make_guardian(self.student.name, relation="Mother")
		frappe.db.set_value("Hostel Guardian", rejected.name, "validation_status", "Rejected")
		before = now_datetime().replace(microsecond=0)
		with capture_notifications() as sent:
			result = self.as_user(self.guard, gate_pass.record_movement, leave, "check_out")
		self.assertEqual(result["status"], "Checked Out")
		self.assertEqual(result["card"]["leave"]["action"], "return")
		checked_out_on = get_datetime(frappe.db.get_value("Home Visit Leave", leave, "checked_out_on"))
		self.assertGreaterEqual(checked_out_on, before)
		self.assertEqual(sent.recipients("leave_checked_out"), {self.guardian.user})
		self.assertNotIn(self.student.user, sent.recipients("leave_checked_out"))

		with capture_notifications() as sent:
			result = self.as_user(self.guard, gate_pass.record_movement, leave, "return")
		self.assertEqual(result["status"], "Returned")
		self.assertEqual(sent.recipients("leave_returned"), {self.guardian.user})

		with self.assertRaises(frappe.ValidationError):
			self.as_user(self.guard, gate_pass.record_movement, leave, "return")
		with self.assertRaises(frappe.ValidationError):
			self.as_user(self.guard, gate_pass.record_movement, leave, "cancel")

	def test_check_out_opens_shortly_before_the_leave(self):
		now = now_datetime()
		leave = frappe._dict(
			name="LV-X",
			status="Approved",
			destination="",
			checked_out_on=None,
			to_datetime=add_to_date(now, hours=40),
		)
		not_yet = gate_pass.leave_details(leave.copy().update(from_datetime=add_to_date(now, hours=5)), now)
		self.assertIsNone(not_yet["action"])
		self.assertTrue(not_yet["note"])
		self.assertEqual(
			gate_pass.leave_details(leave.copy().update(from_datetime=add_to_date(now, hours=1)), now)[
				"action"
			],
			"check_out",
		)
		ended = gate_pass.leave_details(
			leave.copy().update(
				from_datetime=add_to_date(now, hours=-50), to_datetime=add_to_date(now, hours=-1)
			),
			now,
		)
		self.assertIsNone(ended["action"])

		early = self.approved_leave(start_in_hours=5)
		with self.assertRaises(frappe.ValidationError):
			self.as_user(self.guard, gate_pass.record_movement, early, "check_out")

	# ------------------------------------------------------------------ scope

	def test_gates_act_only_for_their_own_hostels(self):
		leave = self.approved_leave()
		other_gate = make_scoped_staff("Security", make_hostel())
		unposted_gate = make_user([SECURITY_ROLE])
		for user in (other_gate, unposted_gate):
			self.assertNotIn(leave, self.board_leaves(user, "leaving"))
			self.assertEqual(self.search(user, self.student.name), [])
			with self.assertRaises(frappe.PermissionError):
				self.as_user(user, gate_pass.record_movement, leave, "check_out")
		self.assertEqual(self.as_user(unposted_gate, gate_pass.get_board)["leaving"], [])
		self.assertEqual(frappe.db.get_value("Home Visit Leave", leave, "status"), "Approved")

	def test_block_gate_covers_only_its_block(self):
		if not people_service("get_staff_scope"):
			self.skipTest("Staff block assignments are not available on this site.")
		block_a, block_b = make_block_room(self.hostel), make_block_room(self.hostel)
		put_in_room(self.student.name, block_a.room)
		neighbour = make_student(self.hostel)
		put_in_room(neighbour.name, block_b.room)
		gate_a = make_scoped_staff("Security", self.hostel, [block_a.block])
		mine, theirs = self.approved_leave(), self.approved_leave(neighbour)

		leaving = self.board_leaves(gate_a, "leaving")
		self.assertIn(mine, leaving)
		self.assertNotIn(theirs, leaving)
		self.assertEqual(self.search(gate_a, neighbour.name), [])
		with self.assertRaises(frappe.PermissionError):
			self.as_user(gate_a, gate_pass.record_movement, theirs, "check_out")
		self.as_user(gate_a, gate_pass.record_movement, mine, "check_out")
		self.assertEqual(frappe.db.get_value("Home Visit Leave", mine, "status"), "Checked Out")

	def test_only_gate_staff_and_wardens_use_the_page(self):
		leave = self.approved_leave()
		for role in (OFFICE_ROLE, MESS_MANAGER_ROLE):
			user = make_user([role])
			for call in (gate_pass.get_board, lambda: gate_pass.search(self.student.name)):
				with self.assertRaises(frappe.PermissionError, msg=role):
					self.as_user(user, call)
			with self.assertRaises(frappe.PermissionError, msg=role):
				self.as_user(user, gate_pass.record_movement, leave, "check_out")
		self.as_user(self.warden, gate_pass.record_movement, leave, "check_out")
		self.assertEqual(frappe.db.get_value("Home Visit Leave", leave, "status"), "Checked Out")

	# ------------------------------------------------------------------ search

	def test_search_by_register_number_name_or_room(self):
		block = make_block_room(self.hostel)
		put_in_room(self.student.name, block.room)
		room_number = frappe.db.get_value("Hostel Room", block.room, "room_number")
		leave = self.approved_leave()
		first_name = self.student.full_name.split()[0]
		for query in (self.student.name, self.student.name[:6].lower(), first_name, room_number):
			results = {card["student"]: card for card in self.search(self.guard, query)}
			self.assertIn(self.student.name, results, query)
		card = results[self.student.name]
		self.assertEqual(card["leave"]["name"], leave)
		self.assertEqual(card["room_number"], room_number)
		self.assertFalse(card["has_photo"])

		self.assertEqual(self.search(self.guard, "a"), [])
		# Wildcards are matched literally, not as "everyone".
		self.assertEqual(self.search(self.guard, "%_"), [])

	def test_search_shows_students_without_leave_and_their_next_leave(self):
		resting = make_student(self.hostel)
		travelling = make_student(self.hostel)
		upcoming = self.approved_leave(travelling, start_in_hours=36)

		card = self.search(self.guard, resting.name)[0]
		self.assertIsNone(card["leave"])
		self.assertIsNone(card["upcoming"])
		card = self.search(self.guard, travelling.name)[0]
		self.assertIsNone(card["leave"])
		self.assertEqual(card["upcoming"]["name"], upcoming)

	# ------------------------------------------------------------------ photo

	def test_photo_is_served_to_gates_of_the_students_hostel_only(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self.as_user(self.guard, gate_pass.student_photo, self.student.name)

		photo = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"photo-{uid(6).lower()}.png",
				"is_private": 1,
				"content": PIXEL_PNG,
				"attached_to_doctype": "Hostel Student",
				"attached_to_name": self.student.name,
				"attached_to_field": "photo",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Hostel Student", self.student.name, "photo", photo.file_url, update_modified=False
		)

		self.as_user(self.guard, gate_pass.student_photo, self.student.name)
		self.assertEqual(frappe.local.response.filecontent, PIXEL_PNG)
		self.assertEqual(frappe.local.response.display_content_as, "inline")
		self.assertTrue(self.search(self.guard, self.student.name)[0]["has_photo"])
		for key in ("type", "filecontent", "filename", "content_type", "display_content_as"):
			frappe.local.response.pop(key, None)

		other_gate = make_scoped_staff("Security", make_hostel())
		with self.assertRaises(frappe.PermissionError):
			self.as_user(other_gate, gate_pass.student_photo, self.student.name)
		with self.assertRaises(frappe.PermissionError):
			self.as_user(make_user([OFFICE_ROLE]), gate_pass.student_photo, self.student.name)
