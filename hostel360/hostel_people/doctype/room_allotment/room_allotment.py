# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, formatdate, getdate, nowdate

from hostel360.hostel_setup.occupancy import UNAVAILABLE_ROOM_STATUSES, refresh_hostel, refresh_room
from hostel360.utils.validation import validate_date_range

# Reason recorded when the daily job closes an allotment whose period has ended.
PERIOD_ENDED_REASON = "Allotment period ended"

# Students in these statuses cannot be given a bed.
NOT_ALLOTTABLE_STUDENT_STATUSES = ("Suspended", "Vacated", "Alumni")

# Statuses the migration may supply for historical allotments that no longer hold a bed.
HISTORICAL_STATUSES = ("Transferred", "Vacated", "Cancelled")


class BedNotAvailableError(frappe.ValidationError):
	pass


class DuplicateAllotmentError(frappe.ValidationError):
	pass


class GenderMismatchError(frappe.ValidationError):
	pass


def in_migration() -> bool:
	return bool(frappe.flags.hostel360_migration)


class RoomAllotment(Document):
	# ------------------------------------------------------------------ validation

	def validate(self):
		if not self.is_historical():
			# Status and vacate details are written by submit, transfer and vacate only.
			self.status = ""
			self.vacated_on = None
			self.vacate_reason = None
		self.set_location_from_bed()
		if not self.academic_year:
			self.academic_year = frappe.db.get_single_value("Hostel Settings", "current_academic_year")
		validate_date_range(self.from_date, self.to_date, "From Date", "To Date")
		if not in_migration():
			self.validate_from_date()
		self.validate_student()
		if not self.is_historical():
			self.validate_gender()
		if self.allotment_type == "Transfer":
			self.validate_previous_allotment()
		elif self.previous_allotment:
			self.previous_allotment = None
		if not self.is_historical():
			self.validate_bed_available()

	def set_location_from_bed(self):
		bed = frappe.db.get_value("Hostel Bed", self.bed, ["room", "hostel", "block"], as_dict=True)
		if not bed:
			frappe.throw(_("Bed {0} does not exist.").format(frappe.bold(self.bed)))
		self.room, self.hostel, self.block = bed.room, bed.hostel, bed.block

	def validate_from_date(self):
		# Back-dating by a few weeks is normal when paperwork catches up with reality.
		if getdate(self.from_date) > getdate(add_days(nowdate(), 60)):
			frappe.throw(_("From Date cannot be more than 60 days ahead."))
		if getdate(self.from_date) < getdate(add_days(nowdate(), -365)):
			frappe.throw(_("From Date cannot be more than a year in the past."))

	def validate_student(self):
		student = frappe.db.get_value(
			"Hostel Student",
			self.student,
			["status", "validation_status", "full_name", "current_hostel", "current_allotment"],
			as_dict=True,
		)
		if not student:
			frappe.throw(_("Student {0} does not exist.").format(frappe.bold(self.student)))
		if self.check_user_access() and not self.admits_returning_student(student):
			# Link fields are not permission-checked on save, and the student's name is sent
			# back with the saved allotment, so a hostel-scoped user must be able to see the
			# student they allot.
			frappe.has_permission("Hostel Student", "read", doc=self.student, throw=True)
		self.student_name = student.full_name
		if self.is_historical():
			return
		if student.status in NOT_ALLOTTABLE_STUDENT_STATUSES:
			frappe.throw(
				_("{0} is {1} and cannot be allotted a bed.").format(student.full_name, _(student.status))
			)
		if student.validation_status == "Rejected":
			frappe.throw(
				_(
					"The registration of {0} was rejected. Correct and validate it before allotting a bed."
				).format(student.full_name)
			)

	def admits_returning_student(self, student) -> bool:
		"""May any hostel's staff give this student a bed, even without seeing the record?

		A student who has left a hostel stays with that hostel's staff (``last_hostel``), but
		coming back next term, often to another hostel, is routine. While the student lives
		in no hostel, staff who may submit an allotment for this bed (the bed's hostel and
		block decide that) may give them the bed. Transfers and students who hold a bed now
		are not affected.
		"""
		return bool(
			self.allotment_type == "New"
			and not student.current_hostel
			and not student.current_allotment
			and frappe.has_permission(self.doctype, "submit", doc=self)
		)

	def validate_gender(self):
		hostel_gender = frappe.get_cached_value("Hostel", self.hostel, "gender")
		student_gender = frappe.db.get_value("Hostel Student", self.student, "gender")
		self.gender = student_gender
		if hostel_gender in ("Male", "Female") and student_gender != hostel_gender:
			frappe.throw(
				_("{0} is a {1} hostel; {2} cannot be allotted a bed here.").format(
					frappe.bold(self.hostel), _(hostel_gender), self.student_name
				),
				GenderMismatchError,
				title=_("Hostel Gender Mismatch"),
			)

	def validate_previous_allotment(self):
		if not self.previous_allotment:
			self.previous_allotment = frappe.db.get_value(
				"Room Allotment",
				{"student": self.student, "docstatus": 1, "status": "Active", "name": ["!=", self.name]},
				"name",
			)
		if not self.previous_allotment:
			frappe.throw(_("{0} has no active allotment to transfer from.").format(self.student_name))

		previous = frappe.db.get_value(
			"Room Allotment",
			self.previous_allotment,
			["student", "docstatus", "status", "bed", "from_date"],
			as_dict=True,
		)
		if previous.student != self.student:
			frappe.throw(
				_("Previous allotment {0} belongs to another student.").format(self.previous_allotment)
			)
		if self.check_user_access():
			# Submitting a transfer closes the previous allotment, so it is an action on that
			# allotment too. Without this a user scoped to one hostel could move a student out
			# of a hostel they cannot see by creating the transfer directly.
			frappe.has_permission("Room Allotment", "submit", doc=self.previous_allotment, throw=True)
		if self.is_historical():
			return
		if previous.docstatus != 1 or previous.status != "Active":
			frappe.throw(
				_("Previous allotment {0} is not active.").format(frappe.bold(self.previous_allotment))
			)
		if previous.bed == self.bed:
			frappe.throw(_("Choose a different bed to transfer to."))
		if getdate(self.from_date) < getdate(previous.from_date):
			frappe.throw(
				_("The transfer date cannot be before the current allotment started on {0}.").format(
					formatdate(previous.from_date)
				)
			)

	def validate_bed_available(self, bed_row=None):
		bed = bed_row or frappe.db.get_value(
			"Hostel Bed", self.bed, ["status", "is_active", "current_allotment", "room"], as_dict=True
		)
		if not bed.is_active:
			frappe.throw(
				_("Bed {0} is no longer in use.").format(frappe.bold(self.bed)), BedNotAvailableError
			)
		if bed.current_allotment and bed.current_allotment != self.name:
			frappe.throw(
				_("Bed {0} is already occupied (allotment {1}).").format(
					frappe.bold(self.bed), bed.current_allotment
				),
				BedNotAvailableError,
				title=_("Bed Not Available"),
			)
		if bed.status != "Vacant":
			frappe.throw(
				_("Bed {0} is {1}. Only a vacant bed can be allotted.").format(
					frappe.bold(self.bed), _(bed.status)
				),
				BedNotAvailableError,
				title=_("Bed Not Available"),
			)

		room = frappe.db.get_value("Hostel Room", bed.room, ["status", "is_active"], as_dict=True)
		if not room.is_active or room.status in UNAVAILABLE_ROOM_STATUSES:
			frappe.throw(
				_("Room {0} is {1} and does not accept allotments.").format(
					frappe.bold(bed.room), _(room.status if room.is_active else "Inactive")
				),
				BedNotAvailableError,
			)
		if not frappe.get_cached_value("Hostel", self.hostel, "is_active"):
			frappe.throw(_("Hostel {0} is inactive.").format(frappe.bold(self.hostel)), BedNotAvailableError)

	def is_historical(self) -> bool:
		"""A migrated allotment that ended before go-live and never holds a bed."""
		return in_migration() and self.status in HISTORICAL_STATUSES

	def check_user_access(self) -> bool:
		"""Whether the records this allotment acts on must be visible to the current user.

		System paths (the data migration, setup scripts) insert with ``ignore_permissions``.
		"""
		return not self.flags.ignore_permissions and not in_migration()

	# ------------------------------------------------------------------ submit / cancel

	def before_submit(self):
		if self.is_historical():
			if self.status in ("Vacated", "Transferred") and not self.vacated_on:
				self.vacated_on = self.to_date
			return
		self.lock_and_check()
		self.status = "Active"

	def lock_and_check(self):
		"""Row-lock the beds and the student, then re-check under the lock.

		Two office staff allotting the same bed (or the same student) at the same moment
		serialise here; the second one sees the first one's allotment and is refused.
		"""
		beds = sorted({self.bed, *([self.get_previous_bed()] if self.allotment_type == "Transfer" else [])})
		locked = {}
		for bed in beds:
			locked[bed] = frappe.db.get_value(
				"Hostel Bed",
				bed,
				["status", "is_active", "current_allotment", "room"],
				as_dict=True,
				for_update=True,
			)
		self.validate_bed_available(locked[self.bed])

		frappe.db.get_value("Hostel Student", self.student, "name", for_update=True)
		active = frappe.get_all(
			"Room Allotment",
			filters={"student": self.student, "docstatus": 1, "status": "Active", "name": ["!=", self.name]},
			pluck="name",
		)
		allowed = {self.previous_allotment} if self.allotment_type == "Transfer" else set()
		others = [name for name in active if name not in allowed]
		if others:
			frappe.throw(
				_("{0} already has an active allotment ({1}). Transfer or vacate it instead.").format(
					self.student_name, ", ".join(others)
				),
				DuplicateAllotmentError,
				title=_("Already Allotted"),
			)
		if self.allotment_type == "Transfer" and self.previous_allotment not in active:
			frappe.throw(_("Previous allotment {0} is no longer active.").format(self.previous_allotment))

	def get_previous_bed(self) -> str | None:
		return frappe.db.get_value("Room Allotment", self.previous_allotment, "bed")

	def on_submit(self):
		if self.is_historical():
			refresh_last_hostel(self.student)
			return

		if self.allotment_type == "Transfer":
			self.close_previous_allotment()

		self.occupy_bed()
		refresh_room(self.room)
		refresh_hostel(self.hostel)

		if not self.flags.skip_notifications:
			self.send_allotment_notification()

	def before_cancel(self):
		if self.status != "Active":
			frappe.throw(
				_(
					"Only an active allotment can be cancelled. Vacated and transferred allotments are kept as history."
				)
			)
		if self.allotment_type == "Transfer":
			frappe.throw(
				_("A transfer cannot be cancelled. Transfer the student again to the correct bed instead.")
			)

	def on_cancel(self):
		self.release_bed()
		self.clear_student_residence()
		self.db_set("status", "Cancelled")
		# A cancelled allotment never happened, so the student's last hostel goes back to the
		# one before it (or to none for a student who never had a bed).
		refresh_last_hostel(self.student)
		refresh_room(self.room)
		refresh_hostel(self.hostel)

	def before_update_after_submit(self):
		if self.has_value_changed("to_date"):
			if self.status != "Active" and not self.flags.allotment_action:
				frappe.throw(_("The end date can only be changed while the allotment is active."))
			validate_date_range(self.from_date, self.to_date, "From Date", "To Date")

	# ------------------------------------------------------------------ bed and student

	def occupy_bed(self):
		frappe.db.set_value(
			"Hostel Bed",
			self.bed,
			{
				"status": "Occupied",
				"current_student": self.student,
				"current_student_name": self.student_name,
				"current_allotment": self.name,
			},
		)
		values = {
			"current_hostel": self.hostel,
			"last_hostel": self.hostel,
			"current_room": self.room,
			"current_bed": self.bed,
			"current_allotment": self.name,
		}
		if frappe.db.get_value("Hostel Student", self.student, "status") == "Applied":
			values["status"] = "Active"
		frappe.db.set_value("Hostel Student", self.student, values)
		claim_unscoped_records(self.student, self.hostel)

	def release_bed(self):
		bed = frappe.db.get_value(
			"Hostel Bed", self.bed, ["current_allotment"], as_dict=True, for_update=True
		)
		if bed and bed.current_allotment == self.name:
			frappe.db.set_value(
				"Hostel Bed",
				self.bed,
				{
					"status": "Vacant",
					"current_student": None,
					"current_student_name": None,
					"current_allotment": None,
				},
			)

	def clear_student_residence(self):
		# last_hostel stays: hostel-scoped staff keep seeing their former residents and staff of
		# other hostels do not (an empty hostel would make the record visible to everybody).
		if frappe.db.get_value("Hostel Student", self.student, "current_allotment") == self.name:
			frappe.db.set_value(
				"Hostel Student",
				self.student,
				{
					"current_hostel": None,
					"current_room": None,
					"current_bed": None,
					"current_allotment": None,
				},
			)

	def close_previous_allotment(self):
		previous = frappe.get_doc("Room Allotment", self.previous_allotment)
		previous.release_bed()
		end_date = add_days(self.from_date, -1)
		if getdate(end_date) < getdate(previous.from_date):
			end_date = previous.from_date
		previous.status = "Transferred"
		previous.vacated_on = self.from_date
		previous.to_date = end_date
		previous.vacate_reason = _("Transferred to bed {0} ({1})").format(self.bed, self.name)
		previous.save_action()
		previous.add_comment("Info", _("Transferred to bed {0} by {1}").format(self.bed, self.name))
		if previous.room != self.room:
			refresh_room(previous.room)
		if previous.hostel != self.hostel:
			refresh_hostel(previous.hostel)

	def save_action(self):
		"""Save a status change on a submitted allotment so it lands in the audit trail."""
		self.flags.ignore_validate_update_after_submit = True
		self.flags.allotment_action = True
		self.flags.ignore_permissions = True
		self.save()

	# ------------------------------------------------------------------ actions

	@frappe.whitelist(methods=["POST"])
	def transfer(self, new_bed: str, from_date: str | None = None, remarks: str | None = None) -> str:
		"""Move the student to another bed. Returns the new allotment."""
		self.check_permission("submit")
		frappe.has_permission("Room Allotment", "create", throw=True)
		# Work from the stored record, not the copy the browser sent with the request.
		self.reload()
		return self.create_transfer(new_bed, from_date, remarks)

	def create_transfer(self, new_bed: str, from_date: str | None = None, remarks: str | None = None) -> str:
		if self.docstatus != 1 or self.status != "Active":
			frappe.throw(_("Only an active allotment can be transferred."))
		if not new_bed:
			frappe.throw(_("Choose the bed to transfer to."))

		start = getdate(from_date or nowdate())
		# The planned end of stay carries over to the new bed.
		to_date = self.to_date if self.to_date and getdate(self.to_date) >= start else None
		new = frappe.get_doc(
			{
				"doctype": "Room Allotment",
				"student": self.student,
				"academic_year": self.academic_year,
				"allotment_type": "Transfer",
				"previous_allotment": self.name,
				"bed": new_bed,
				"from_date": start,
				"to_date": to_date,
				"remarks": remarks,
			}
		)
		new.flags.skip_notifications = self.flags.skip_notifications
		new.insert()
		new.submit()
		return new.name

	@frappe.whitelist(methods=["POST"])
	def vacate(self, vacated_on: str | None = None, reason: str | None = None) -> None:
		"""The student leaves the bed. The allotment stays as history."""
		self.check_permission("submit")
		reason = (reason or "").strip()
		if not reason:
			frappe.throw(_("Please enter the reason for vacating."))
		self.reload()
		self.vacate_allotment(vacated_on or nowdate(), reason)

	def vacate_allotment(self, vacated_on, reason: str) -> None:
		state = frappe.db.get_value(
			"Room Allotment", self.name, ["docstatus", "status"], as_dict=True, for_update=True
		)
		if state.docstatus != 1 or state.status != "Active":
			frappe.throw(_("Allotment {0} is not active.").format(frappe.bold(self.name)))
		self.reload()

		vacated_on = getdate(vacated_on)
		if vacated_on < getdate(self.from_date):
			frappe.throw(
				_("Vacated On cannot be before the allotment started on {0}.").format(
					formatdate(self.from_date)
				)
			)
		if vacated_on > getdate(nowdate()) and not in_migration():
			frappe.throw(_("Vacated On cannot be in the future."))

		self.release_bed()
		self.clear_student_residence()
		self.status = "Vacated"
		self.vacated_on = vacated_on
		self.vacate_reason = reason
		if not self.to_date or getdate(self.to_date) > vacated_on:
			self.to_date = vacated_on
		self.save_action()
		self.add_comment("Info", _("Vacated on {0}: {1}").format(formatdate(vacated_on), reason))
		refresh_room(self.room)
		refresh_hostel(self.hostel)

	# ------------------------------------------------------------------ messages

	def send_allotment_notification(self):
		from hostel360.hostel_people.notifications import notify_student

		hostel_name = frappe.get_cached_value("Hostel", self.hostel, "hostel_name")
		room_number = frappe.db.get_value("Hostel Room", self.room, "room_number")
		bed_label = frappe.db.get_value("Hostel Bed", self.bed, "bed_label")
		notify_student(
			"room_allotted",
			self.student,
			{
				"student_name": self.student_name,
				"hostel": self.hostel,
				"hostel_name": hostel_name,
				"room": self.room,
				"room_number": room_number,
				"bed": self.bed,
				"bed_label": bed_label,
				"from_date": formatdate(self.from_date),
				"allotment_type": self.allotment_type,
			},
			reference_doctype=self.doctype,
			reference_name=self.name,
		)


def last_allotted_hostel(student: str) -> str | None:
	"""Hostel of the student's current allotment, else of the most recent one that ended."""
	rows = frappe.get_all(
		"Room Allotment",
		filters={"student": student, "docstatus": 1, "status": ["!=", "Cancelled"]},
		fields=["hostel", "status"],
		order_by="from_date desc, creation desc",
		limit=20,
	)
	for row in rows:
		if row.status == "Active":
			return row.hostel
	return rows[0].hostel if rows else None


def refresh_last_hostel(student: str) -> None:
	"""Re-derive ``last_hostel`` from the allotment history (the current hostel wins)."""
	current = frappe.db.get_value("Hostel Student", student, ["current_hostel", "last_hostel"], as_dict=True)
	if not current:
		return
	wanted = current.current_hostel or last_allotted_hostel(student)
	if (current.last_hostel or None) != (wanted or None):
		frappe.db.set_value("Hostel Student", student, "last_hostel", wanted, update_modified=False)


# Records that copy the student's hostel when they are created. Ones created before the
# student had a bed carry no hostel, which Frappe treats as visible to every hostel's staff.
HOSTEL_SCOPED_STUDENT_RECORDS = ("Hostel Fee Invoice", "Hostel Payment")


def claim_unscoped_records(student: str, hostel: str) -> None:
	"""File the student's hostel-less records under the hostel they have just moved into."""
	for doctype in HOSTEL_SCOPED_STUDENT_RECORDS:
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		if not (meta.has_field("student") and meta.has_field("hostel")):
			continue
		table = frappe.qb.DocType(doctype)
		(
			frappe.qb.update(table)
			.set(table.hostel, hostel)
			.where(table.student == student)
			.where((table.hostel.isnull()) | (table.hostel == ""))
		).run()


def allot_bed(
	student: str,
	bed: str,
	from_date: str | None = None,
	to_date: str | None = None,
	remarks: str | None = None,
	academic_year: str | None = None,
) -> str:
	"""Create and submit a new allotment as the current user (permissions apply)."""
	doc = frappe.get_doc(
		{
			"doctype": "Room Allotment",
			"student": student,
			"bed": bed,
			"allotment_type": "New",
			"from_date": from_date or nowdate(),
			"to_date": to_date,
			"remarks": remarks,
			"academic_year": academic_year,
		}
	)
	doc.insert()
	doc.submit()
	return doc.name
