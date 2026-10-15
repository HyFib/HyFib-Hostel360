# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from hostel360.hostel_people.doctype.room_allotment.room_allotment import (
	PERIOD_ENDED_REASON,
	BedNotAvailableError,
	DuplicateAllotmentError,
	GenderMismatchError,
)
from hostel360.hostel_people.tasks import close_ended_allotments
from hostel360.hostel_people.testing import allot, beds_of, make_hostel, make_room, make_student


class TestRoomAllotment(IntegrationTestCase):
	def setUp(self):
		self.hostel = make_hostel("Male")
		self.room = make_room(self.hostel.name, capacity=2)
		self.bed_a, self.bed_b = beds_of(self.room.name)

	def test_allotment_occupies_bed_and_updates_student(self):
		student = make_student("Male", status="Applied")
		allotment = allot(student.name, self.bed_a)

		self.assertEqual(allotment.status, "Active")
		self.assertEqual(allotment.hostel, self.hostel.name)
		self.assertEqual(allotment.room, self.room.name)
		bed = frappe.db.get_value("Hostel Bed", self.bed_a, ["status", "current_student", "current_allotment"], as_dict=True)
		self.assertEqual((bed.status, bed.current_student, bed.current_allotment), ("Occupied", student.name, allotment.name))

		student.reload()
		self.assertEqual(student.current_hostel, self.hostel.name)
		self.assertEqual(student.current_room, self.room.name)
		self.assertEqual(student.current_bed, self.bed_a)
		self.assertEqual(student.current_allotment, allotment.name)
		self.assertEqual(student.status, "Active")
		self.assertEqual(frappe.db.get_value("Hostel", self.hostel.name, "occupied_beds"), 1)

	def test_occupied_bed_is_refused(self):
		allot(make_student("Male").name, self.bed_a)
		with self.assertRaises(BedNotAvailableError):
			allot(make_student("Male").name, self.bed_a)

	def test_bed_under_maintenance_or_room_blocked_is_refused(self):
		frappe.db.set_value("Hostel Bed", self.bed_a, "status", "Maintenance")
		with self.assertRaises(BedNotAvailableError):
			allot(make_student("Male").name, self.bed_a)

		self.room.reload()
		self.room.status = "Blocked"
		self.room.save()
		with self.assertRaises(BedNotAvailableError):
			allot(make_student("Male").name, self.bed_b)

	def test_student_cannot_hold_two_beds(self):
		student = make_student("Male")
		allot(student.name, self.bed_a)
		with self.assertRaises(DuplicateAllotmentError):
			allot(student.name, self.bed_b)
		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_b, "status"), "Vacant")

	def test_draft_for_occupied_bed_cannot_be_submitted(self):
		first = frappe.get_doc(
			{"doctype": "Room Allotment", "student": make_student("Male").name, "bed": self.bed_a, "from_date": nowdate()}
		).insert()
		second = frappe.get_doc(
			{"doctype": "Room Allotment", "student": make_student("Male").name, "bed": self.bed_a, "from_date": nowdate()}
		).insert()
		first.submit()
		with self.assertRaises(BedNotAvailableError):
			second.submit()

	def test_gender_must_match_hostel(self):
		with self.assertRaises(GenderMismatchError):
			allot(make_student("Female").name, self.bed_a)

		mixed = make_hostel("Mixed")
		mixed_bed = beds_of(make_room(mixed.name, capacity=1).name)[0]
		self.assertEqual(allot(make_student("Female").name, mixed_bed).status, "Active")

	def test_inactive_students_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			allot(make_student("Male", status="Alumni").name, self.bed_a)

	def test_transfer_keeps_history(self):
		student = make_student("Male")
		other_room = make_room(self.hostel.name, capacity=1)
		new_bed = beds_of(other_room.name)[0]
		first = allot(student.name, self.bed_a, from_date=add_days(nowdate(), -30))

		new_name = first.transfer(new_bed=new_bed, from_date=nowdate(), remarks="Requested by warden")
		first.reload()
		second = frappe.get_doc("Room Allotment", new_name)

		self.assertEqual(first.status, "Transferred")
		self.assertEqual(getdate(first.vacated_on), getdate(nowdate()))
		self.assertEqual(getdate(first.to_date), getdate(add_days(nowdate(), -1)))
		self.assertEqual(second.status, "Active")
		self.assertEqual(second.allotment_type, "Transfer")
		self.assertEqual(second.previous_allotment, first.name)

		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_a, "status"), "Vacant")
		self.assertEqual(frappe.db.get_value("Hostel Bed", new_bed, "current_allotment"), second.name)
		student.reload()
		self.assertEqual(student.current_bed, new_bed)
		self.assertEqual(student.current_allotment, second.name)
		self.assertEqual(frappe.db.get_value("Hostel Room", self.room.name, "occupied_beds"), 0)
		self.assertEqual(frappe.db.get_value("Hostel Room", other_room.name, "status"), "Full")

		history = frappe.get_all("Room Allotment", filters={"student": student.name, "docstatus": 1}, pluck="status")
		self.assertEqual(sorted(history), ["Active", "Transferred"])

		# Transferred history is permanent.
		self.assertRaises(frappe.ValidationError, first.cancel)
		self.assertRaises(frappe.ValidationError, second.cancel)

	def test_transfer_to_occupied_bed_is_refused(self):
		first = allot(make_student("Male").name, self.bed_a)
		allot(make_student("Male").name, self.bed_b)
		with self.assertRaises(BedNotAvailableError):
			first.transfer(new_bed=self.bed_b)
		first.reload()
		self.assertEqual(first.status, "Active")
		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_a, "current_allotment"), first.name)

	def test_vacate_releases_bed(self):
		student = make_student("Male")
		allotment = allot(student.name, self.bed_a, from_date=add_days(nowdate(), -10))

		self.assertRaises(frappe.ValidationError, allotment.vacate, vacated_on=nowdate(), reason="")
		self.assertRaises(frappe.ValidationError, allotment.vacate, vacated_on=add_days(nowdate(), 2), reason="Leaving")
		self.assertRaises(frappe.ValidationError, allotment.vacate, vacated_on=add_days(nowdate(), -20), reason="Leaving")

		allotment.vacate(vacated_on=nowdate(), reason="Course completed")
		allotment.reload()
		self.assertEqual(allotment.status, "Vacated")
		self.assertEqual(allotment.vacate_reason, "Course completed")
		self.assertEqual(getdate(allotment.to_date), getdate(nowdate()))
		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_a, "status"), "Vacant")
		student.reload()
		self.assertIsNone(student.current_bed)
		self.assertIsNone(student.current_allotment)
		self.assertRaises(frappe.ValidationError, allotment.vacate, vacated_on=nowdate(), reason="Again")

		# The bed is free for the next student, and the old record stays.
		allot(make_student("Male").name, self.bed_a)
		self.assertTrue(frappe.db.exists("Room Allotment", {"name": allotment.name, "status": "Vacated"}))

	def test_cancel_releases_bed(self):
		student = make_student("Male")
		allotment = allot(student.name, self.bed_a)
		allotment.cancel()
		self.assertEqual(frappe.db.get_value("Room Allotment", allotment.name, "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_a, "status"), "Vacant")
		self.assertIsNone(frappe.db.get_value("Hostel Student", student.name, "current_allotment"))

	def test_extending_to_date_after_submit(self):
		allotment = allot(make_student("Male").name, self.bed_a, from_date=add_days(nowdate(), -3))
		allotment.to_date = add_days(nowdate(), 30)
		allotment.save()
		allotment.to_date = add_days(nowdate(), -10)
		self.assertRaises(frappe.ValidationError, allotment.save)

	def test_close_ended_allotments(self):
		ended = allot(
			make_student("Male").name,
			self.bed_a,
			from_date=add_days(nowdate(), -20),
			to_date=add_days(nowdate(), -1),
		)
		ongoing = allot(make_student("Male").name, self.bed_b, to_date=add_days(nowdate(), 10))

		# The job commits after each allotment; keep the test inside its own transaction.
		with patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback"):
			close_ended_allotments()

		ended.reload()
		self.assertEqual(ended.status, "Vacated")
		self.assertEqual(ended.vacate_reason, PERIOD_ENDED_REASON)
		self.assertEqual(getdate(ended.vacated_on), getdate(add_days(nowdate(), -1)))
		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_a, "status"), "Vacant")
		self.assertEqual(frappe.db.get_value("Room Allotment", ongoing.name, "status"), "Active")

	def test_migration_accepts_historical_allotments(self):
		student = make_student("Male")
		frappe.flags.hostel360_migration = True
		try:
			old = frappe.get_doc(
				{
					"doctype": "Room Allotment",
					"student": student.name,
					"bed": self.bed_a,
					"from_date": "2019-06-01",
					"to_date": "2020-05-31",
					"status": "Vacated",
					"vacated_on": "2020-05-31",
					"vacate_reason": "Migrated from the previous system",
				}
			).insert()
			old.submit()
			current = allot(student.name, self.bed_b, from_date=add_days(nowdate(), -400))
		finally:
			frappe.flags.hostel360_migration = False

		self.assertEqual(frappe.db.get_value("Room Allotment", old.name, "status"), "Vacated")
		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_a, "status"), "Vacant")
		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_b, "current_allotment"), current.name)
		self.assertEqual(frappe.db.get_value("Hostel Student", student.name, "current_allotment"), current.name)

	def test_from_date_window_outside_migration(self):
		with self.assertRaises(frappe.ValidationError):
			allot(make_student("Male").name, self.bed_a, from_date=add_days(nowdate(), 90))

	def test_actions_ignore_tampered_client_copy(self):
		allotment = allot(make_student("Male").name, self.bed_a, from_date=add_days(nowdate(), -2))
		allotment.bed = self.bed_b
		allotment.student = make_student("Male").name
		allotment.vacate(vacated_on=nowdate(), reason="Left the hostel")
		stored = frappe.db.get_value("Room Allotment", allotment.name, ["bed", "student", "status"], as_dict=True)
		self.assertEqual(stored.bed, self.bed_a)
		self.assertEqual(stored.status, "Vacated")
		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_a, "status"), "Vacant")

	def test_draft_cannot_carry_a_status(self):
		draft = frappe.get_doc(
			{
				"doctype": "Room Allotment",
				"student": make_student("Male").name,
				"bed": self.bed_a,
				"from_date": nowdate(),
				"status": "Vacated",
				"vacated_on": nowdate(),
			}
		).insert()
		self.assertEqual(draft.status, "")
		self.assertIsNone(draft.vacated_on)
		draft.submit()
		self.assertEqual(draft.status, "Active")
		self.assertEqual(frappe.db.get_value("Hostel Bed", self.bed_a, "current_allotment"), draft.name)

	def test_failed_auto_close_raises_an_ops_alert(self):
		if not frappe.db.exists("DocType", "Ops Alert"):
			self.skipTest("Hostel Operations is not installed")
		from hostel360.hostel_people.tasks import ALERT_KEY

		allot(make_student("Male").name, self.bed_a, from_date=add_days(nowdate(), -20), to_date=add_days(nowdate(), -1))
		target = "hostel360.hostel_people.doctype.room_allotment.room_allotment.RoomAllotment.vacate_allotment"
		with (
			patch(target, side_effect=frappe.ValidationError("bed locked")),
			patch.object(frappe.db, "commit"),
			patch.object(frappe.db, "rollback"),
			patch.object(frappe, "log_error"),
		):
			close_ended_allotments()
		self.assertTrue(frappe.db.exists("Ops Alert", {"alert_key": ALERT_KEY, "status": ["!=", "Resolved"]}))

		with patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback"):
			close_ended_allotments()
		self.assertFalse(frappe.db.exists("Ops Alert", {"alert_key": ALERT_KEY, "status": ["in", ["Open", "Acknowledged"]]}))
