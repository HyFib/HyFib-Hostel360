# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, date_diff, format_date, getdate, nowdate

from hostel360.hostel_mess.utils import (
	approver_recipients,
	assert_period_open,
	date_range_days,
	get_mess_for_hostel,
	get_mess_settings,
	get_reduction_approver_role,
	get_reduction_decider_roles,
	get_student_hostel,
	in_migration,
	iter_months,
	send_notification,
)
from hostel360.utils.validation import validate_date_range

ACTIVE_STATUSES = ("Pending", "Approved")
LOCKED_FIELDS = ("student", "from_date", "to_date", "reason")


class MessReductionApplication(Document):
	def before_insert(self):
		self.applied_by = self.applied_by or frappe.session.user
		if not in_migration():
			self.status = "Pending"

	def validate(self):
		validate_date_range(self.from_date, self.to_date)
		self.set_derived_values()
		self.validate_status_change()
		self.validate_overlap()

		dates_changed = self.is_new() or any(self.has_value_changed(f) for f in LOCKED_FIELDS)
		if dates_changed and self.status == "Pending" and not in_migration():
			settings = get_mess_settings()
			self.validate_student()
			self.validate_minimum_days(settings)
			self.validate_notice(settings)
			self.validate_monthly_cap(settings)
			assert_period_open(self.mess, self.from_date, _("Mess reductions"))
		self.validate_home_visit_leave()

	def after_insert(self):
		if self.status == "Pending":
			self.notify_approvers()

	def on_trash(self):
		if self.status == "Approved" and not in_migration():
			frappe.throw(_("An approved reduction cannot be deleted. Cancel it instead."))

	# ------------------------------------------------------------------ validation

	def set_derived_values(self):
		self.number_of_days = date_diff(self.to_date, self.from_date) + 1
		if not frappe.db.exists("Hostel Student", self.student):
			frappe.throw(_("Student {0} does not exist.").format(self.student))
		self.student_name = frappe.db.get_value("Hostel Student", self.student, "full_name")
		if self.is_new() or self.has_value_changed("student"):
			# Kept after the student vacates, so the hostel's staff go on seeing the record.
			self.hostel = get_student_hostel(self.student)
			self.mess = get_mess_for_hostel(self.hostel)
		elif not self.hostel:
			self.hostel = get_student_hostel(self.student)

	def validate_status_change(self):
		if in_migration():
			return
		if self.is_new():
			if self.status != "Pending":
				frappe.throw(_("A new application must be pending."))
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if before.status != self.status and not self.flags.status_change:
			frappe.throw(_("Use the Approve, Reject or Cancel actions to change the status."))
		if before.status != "Pending" and any(
			str(before.get(f) or "") != str(self.get(f) or "") for f in LOCKED_FIELDS
		):
			frappe.throw(_("Only a pending application can be changed."))

	def validate_student(self):
		student = frappe.db.get_value(
			"Hostel Student", self.student, ["status", "mess_enrolled", "current_hostel"], as_dict=True
		)
		if student.status != "Active":
			frappe.throw(_("Mess reduction is available only to active residents."))
		if not cint(student.mess_enrolled):
			frappe.throw(_("{0} is not enrolled in the mess.").format(self.student_name or self.student))
		if not self.mess:
			frappe.throw(
				_("No mess is set up for hostel {0}. Please contact the hostel office.").format(
					student.current_hostel or _("(not allotted)")
				)
			)

	def validate_overlap(self):
		if self.status not in ACTIVE_STATUSES:
			return
		clash = frappe.db.get_value(
			"Mess Reduction Application",
			{
				"student": self.student,
				"status": ["in", ACTIVE_STATUSES],
				"name": ["!=", self.name or ""],
				"from_date": ["<=", self.to_date],
				"to_date": [">=", self.from_date],
			},
			["name", "from_date", "to_date", "status"],
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("These dates overlap {0} application {1} ({2} to {3}).").format(
					_(clash.status).lower(),
					frappe.bold(clash.name),
					format_date(clash.from_date),
					format_date(clash.to_date),
				),
				title=_("Overlapping Application"),
			)

	def validate_minimum_days(self, settings):
		minimum = cint(settings.reduction_min_days)
		if minimum and self.number_of_days < minimum:
			frappe.throw(
				_("A mess reduction must be for at least {0} days; this application is for {1}.").format(
					minimum, self.number_of_days
				),
				title=_("Too Short"),
			)

	def validate_notice(self, settings):
		if self.flags.skip_notice_check or self.home_visit_leave or self.applicant_can_decide(settings):
			return
		earliest = add_days(nowdate(), cint(settings.reduction_advance_notice_days))
		if getdate(self.from_date) < getdate(earliest):
			frappe.throw(
				_(
					"Please apply at least {0} day(s) in advance. The earliest start date you can choose is {1}."
				).format(cint(settings.reduction_advance_notice_days), format_date(earliest)),
				title=_("Not Enough Notice"),
			)

	def validate_monthly_cap(self, settings):
		cap = cint(settings.max_reduction_days_per_month)
		if not cap:
			return
		own_days = date_range_days(self.from_date, self.to_date)
		months = list(iter_months(self.from_date, self.to_date))
		others = frappe.get_all(
			"Mess Reduction Application",
			filters={
				"student": self.student,
				"status": ["in", ACTIVE_STATUSES],
				"name": ["!=", self.name or ""],
				"from_date": ["<=", months[-1][1]],
				"to_date": [">=", months[0][0]],
			},
			fields=["from_date", "to_date"],
		)
		other_days = set()
		for row in others:
			other_days |= date_range_days(row.from_date, row.to_date)
		for first, last in months:
			month_days = date_range_days(first, last)
			used = len(other_days & month_days)
			wanted = len(own_days & month_days)
			if used + wanted > cap:
				frappe.throw(
					_(
						"At most {0} reduction days are allowed in {1}. {2} days are already applied for, so up to {3} more can be requested."
					).format(cap, getdate(first).strftime("%B %Y"), used, max(cap - used, 0)),
					title=_("Monthly Limit Reached"),
				)

	def validate_home_visit_leave(self):
		if not self.home_visit_leave:
			return
		leave_student = frappe.db.get_value("Home Visit Leave", self.home_visit_leave, "student")
		if leave_student and leave_student != self.student:
			frappe.throw(_("Home visit leave {0} belongs to another student.").format(self.home_visit_leave))

	def applicant_can_decide(self, settings=None) -> bool:
		user = self.applied_by or frappe.session.user
		if user == "Administrator":
			return True
		return bool(set(frappe.get_roles(user)) & get_reduction_decider_roles(settings))

	# ------------------------------------------------------------------ notifications

	def notification_context(self) -> dict:
		"""Variables for the mess reduction message templates (raw values; templates format them)."""
		return {
			"application": self.name,
			"student": self.student,
			"student_name": self.student_name,
			"hostel": self.hostel,
			"mess": self.mess,
			"from_date": str(getdate(self.from_date)),
			"to_date": str(getdate(self.to_date)),
			"number_of_days": self.number_of_days,
			"reason": self.reason,
			"status": self.status,
			"remarks": self.approval_remarks or "",
		}

	def notify_approvers(self):
		send_notification(
			"mess_reduction_submitted",
			approver_recipients(get_reduction_approver_role(), hostel=self.hostel),
			self.notification_context(),
			reference_doctype=self.doctype,
			reference_name=self.name,
		)


@frappe.whitelist(methods=["POST"])
def decide(name: str, approve: int | bool | str, remarks: str | None = None) -> dict:
	"""Approve or reject from the desk form."""
	from hostel360.hostel_mess.services import decide_mess_reduction

	decide_mess_reduction(name, approve, remarks, frappe.session.user)
	return {"status": frappe.db.get_value("Mess Reduction Application", name, "status")}


@frappe.whitelist(methods=["POST"])
def cancel_application(name: str) -> dict:
	"""Cancel from the desk form."""
	from hostel360.hostel_mess.services import cancel_mess_reduction

	cancel_mess_reduction(name, frappe.session.user)
	return {"status": "Cancelled"}
