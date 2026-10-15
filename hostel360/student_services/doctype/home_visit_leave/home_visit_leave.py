# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, date_diff, get_datetime, getdate, now_datetime

from hostel360.setup.roles import DEPUTY_WARDEN_ROLE, MESS_MANAGER_ROLE, SECURITY_ROLE
from hostel360.student_services import notifications
from hostel360.student_services.utils import (
	assert_student_eligible,
	assert_unchanged,
	assert_warden_approver,
	get_mess_setting,
	get_role_users,
	get_setting,
	get_staff_scope,
	get_student,
	get_warden_approvers,
	in_migration,
	is_staff_user,
	load_for_action,
	recheck_scope,
	scope_covers_record,
	student_hostel,
)

PENDING_STATUSES = ("Pending Parent Approval", "Pending Warden Approval")
# Leaves that still hold the student's calendar: a new leave may not overlap them.
ACTIVE_STATUSES = (*PENDING_STATUSES, "Approved", "Checked Out", "Overdue")
CANCELLABLE_STATUSES = (*PENDING_STATUSES, "Approved")
# A return can be recorded for these; "Approved" covers a student who left unrecorded.
RETURNABLE_STATUSES = ("Approved", "Checked Out", "Overdue")
# Students may apply for a leave that starts a little in the past (the form is often filled
# in at the gate), but not for one that started long ago.
BACKDATE_TOLERANCE_MINUTES = 60
# A student may be let out a little before the leave starts (an early bus), never once it
# has ended: going home on other days needs a new leave.
EARLY_CHECK_OUT_MINUTES = 120
# Allowance for a desk clock running ahead of the server when a time is typed in.
FUTURE_TOLERANCE_MINUTES = 5
MOVEMENT_ACTIONS = ("check_out", "return")
# For mess reduction a travel day counts as a day away when most of its meals are missed.
MESS_DAY_CUTOFF_HOUR = 12
REDUCTION = "Mess Reduction Application"
REDUCTION_OPEN_STATUSES = ("Pending", "Approved")

# Taken from the student and the applicant when the leave is created; never edited after.
IDENTITY_FIELDS = ("student", "hostel", "room", "applied_by", "applied_by_user")
# Set only by the decision and movement actions (save_status), never by an ordinary save.
WORKFLOW_FIELDS = (
	"status",
	"parent_approval_status",
	"parent_guardian",
	"parent_action_on",
	"parent_remarks",
	"warden_approval_status",
	"warden_user",
	"warden_action_on",
	"warden_remarks",
	"cancelled_by",
	"checked_out_on",
	"returned_on",
	"overdue_since",
	"mess_reduction",
)


class HomeVisitLeave(Document):
	def validate(self):
		self.validate_dates()
		if self.is_new():
			student = self.set_student_details()
		else:
			self.validate_protected_fields()
		# Before any check whose message would describe another hostel's records.
		recheck_scope(self)
		if self.is_new() and not in_migration():
			assert_student_eligible(student, _("Leave"))
		self.validate_dates_locked()
		if not in_migration() and (
			self.is_new() or self.has_value_changed("from_datetime") or self.has_value_changed("to_datetime")
		):
			self.validate_not_backdated()
			self.validate_overlap()
		self.number_of_days = date_diff(getdate(self.to_datetime), getdate(self.from_datetime)) + 1
		self.mess_reduction_days = self.get_mess_reduction_window()[2] if self.apply_mess_reduction else 0
		if self.is_new():
			self.set_initial_status()

	def after_insert(self):
		self.notify_submitted()

	# ------------------------------------------------------------------ validation

	def validate_dates(self):
		if not (self.from_datetime and self.to_datetime):
			return
		if get_datetime(self.to_datetime) <= get_datetime(self.from_datetime):
			frappe.throw(_("Return date and time must be after the leaving date and time."))

	def set_student_details(self) -> frappe._dict:
		student = get_student(self.student)
		self.student_name = student.full_name
		if not (in_migration() and self.hostel):
			self.hostel = student_hostel(student)
			self.room = student.get("current_room")
		return student

	def validate_protected_fields(self):
		if in_migration():
			return
		assert_unchanged(
			self, IDENTITY_FIELDS, _("{0} cannot be changed. Cancel this leave and apply again if they are wrong.")
		)
		if not self.flags.leave_transition:
			assert_unchanged(
				self,
				WORKFLOW_FIELDS,
				_("{0} can only be changed with the Approve, Reject, Checked Out, Returned and Cancel Leave actions."),
			)

	def validate_dates_locked(self):
		if self.is_new() or in_migration():
			return
		before = self.get_doc_before_save()
		if not before or before.status in PENDING_STATUSES:
			return
		for fieldname in ("from_datetime", "to_datetime"):
			if self.has_value_changed(fieldname):
				frappe.throw(
					_("Leave dates cannot be changed once the leave is {0}. Cancel it and apply again.").format(
						_(before.status)
					)
				)

	def validate_not_backdated(self):
		if self.applied_by == "Warden" or not self.is_new():
			return
		earliest = add_to_date(now_datetime(), minutes=-BACKDATE_TOLERANCE_MINUTES)
		if get_datetime(self.from_datetime) < earliest:
			frappe.throw(_("Leave cannot start in the past. Please check the leaving date and time."))

	def validate_overlap(self):
		clash = frappe.db.get_value(
			"Home Visit Leave",
			{
				"student": self.student,
				"name": ["!=", self.name or ""],
				"status": ["in", ACTIVE_STATUSES],
				"from_datetime": ["<", self.to_datetime],
				"to_datetime": [">", self.from_datetime],
			},
			["name", "status"],
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("These dates overlap leave {0}, which is {1}.").format(clash.name, _(clash.status)),
				title=_("Overlapping Leave"),
			)

	def set_initial_status(self):
		if in_migration() and self.status:
			return
		self.applied_by = self.applied_by or "Student"
		self.applied_by_user = self.applied_by_user or frappe.session.user
		# A new leave carries no decision or movement, whatever the request said.
		for fieldname in WORKFLOW_FIELDS:
			if not (fieldname == "parent_guardian" and self.applied_by == "Parent"):
				self.set(fieldname, None)
		self.warden_approval_status = "Pending"
		approver_guardians = self.get_approver_guardians()

		if self.applied_by == "Parent":
			if not self.parent_guardian or self.parent_guardian not in approver_guardians:
				frappe.throw(
					_("Only a parent or guardian linked to the student and allowed to approve leave can apply."),
					frappe.PermissionError,
				)
			# A leave applied by the parent carries the parent's consent.
			self.parent_approval_status = "Approved"
			self.parent_action_on = now_datetime()
			self.status = "Pending Warden Approval"
		elif self.applied_by == "Student" and frappe.utils.cint(get_setting("leave_requires_parent_approval")):
			if not approver_guardians:
				frappe.throw(
					_(
						"Leave needs a parent's approval, but no parent or guardian who can approve leave is "
						"linked to this student. Please contact the hostel office."
					)
				)
			self.parent_approval_status = "Pending"
			self.status = "Pending Parent Approval"
		else:
			self.parent_approval_status = "Not Required"
			self.status = "Pending Warden Approval"

	# ------------------------------------------------------------------ helpers

	def get_approver_guardians(self) -> list[str]:
		from hostel360.utils.context import get_guardians

		return get_guardians(self.student, leave_approvers_only=True)

	def get_mess_reduction_window(self):
		"""(from_date, to_date, days) of full mess days missed during the leave."""
		if not (self.from_datetime and self.to_datetime):
			return None, None, 0
		start, end = get_datetime(self.from_datetime), get_datetime(self.to_datetime)
		first = start.date() if start.hour < MESS_DAY_CUTOFF_HOUR else start.date() + timedelta(days=1)
		last = end.date() if end.hour >= MESS_DAY_CUTOFF_HOUR else end.date() - timedelta(days=1)
		if last < first:
			return None, None, 0
		return first, last, (last - first).days + 1

	def save_status(self):
		self.flags.ignore_permissions = True
		self.flags.leave_transition = True
		try:
			self.save()
		finally:
			self.flags.leave_transition = False

	# ------------------------------------------------------------------ decisions

	def apply_parent_decision(self, guardian: str, approve: bool, remarks: str | None = None):
		if self.status != "Pending Parent Approval":
			frappe.throw(_("This leave is not waiting for a parent's approval (it is {0}).").format(_(self.status)))
		if guardian not in self.get_approver_guardians():
			frappe.throw(_("You are not allowed to approve leave for this student."), frappe.PermissionError)
		if not approve and not (remarks or "").strip():
			frappe.throw(_("Please give a reason for rejecting the leave."))

		self.parent_guardian = guardian
		self.parent_approval_status = "Approved" if approve else "Rejected"
		self.parent_action_on = now_datetime()
		self.parent_remarks = (remarks or "").strip() or None
		self.status = "Pending Warden Approval" if approve else "Rejected"
		self.save_status()

		if approve:
			self.notify_wardens()
		else:
			self.notify_decided()

	def apply_warden_decision(self, approve: bool, remarks: str | None, user: str):
		assert_warden_approver(user, self.hostel, self.student)
		if approve and self.status != "Pending Warden Approval":
			if self.status == "Pending Parent Approval":
				frappe.throw(_("This leave is still waiting for the parent's approval."))
			frappe.throw(_("This leave has already been decided (it is {0}).").format(_(self.status)))
		if not approve and self.status not in PENDING_STATUSES:
			frappe.throw(_("This leave has already been decided (it is {0}).").format(_(self.status)))
		if not approve and not (remarks or "").strip():
			frappe.throw(_("Please give a reason for rejecting the leave."))

		self.warden_approval_status = "Approved" if approve else "Rejected"
		self.warden_user = user
		self.warden_action_on = now_datetime()
		self.warden_remarks = (remarks or "").strip() or None
		self.status = "Approved" if approve else "Rejected"
		self.save_status()

		if approve and self.apply_mess_reduction:
			self.create_mess_reduction(user)
		self.notify_decided()

	def create_mess_reduction(self, user: str):
		"""Raise the Mess Reduction Application for the days away; never blocks the approval."""
		if self.mess_reduction:
			return
		from_date, to_date, days = self.get_mess_reduction_window()
		minimum = frappe.utils.cint(get_mess_setting("reduction_min_days", 3))
		if days < max(minimum, 1):
			self.add_comment(
				"Info",
				_("Mess reduction was not applied: {0} mess day(s) away is less than the minimum of {1}.").format(
					days, minimum
				),
			)
			return
		try:
			from hostel360.hostel_mess.services import apply_mess_reduction
		except ImportError:
			frappe.log_error(title="Hostel360: mess reduction service unavailable", reference_doctype=self.doctype)
			return

		frappe.db.savepoint("leave_mess_reduction")
		try:
			name = apply_mess_reduction(
				self.student,
				from_date,
				to_date,
				"Home Visit",
				description=_("Home visit leave {0}").format(self.name),
				home_visit_leave=self.name,
				user=user,
			)
		except frappe.ValidationError as e:
			frappe.db.rollback(save_point="leave_mess_reduction")
			frappe.clear_last_message()
			message = _("Leave approved, but the mess reduction could not be applied: {0}").format(str(e))
			self.add_comment("Info", message)
			frappe.msgprint(message, indicator="orange", alert=True)
			return
		self.db_set("mess_reduction", name)

	def cancel_by(self, user: str):
		if self.status not in CANCELLABLE_STATUSES:
			frappe.throw(_("Only leaves that have not started can be cancelled (this one is {0}).").format(_(self.status)))
		self.status = "Cancelled"
		self.cancelled_by = user
		self.save_status()
		self.cancel_mess_reduction(user)

	def cancel_mess_reduction(self, user: str):
		"""Withdraw the mess reduction raised for this leave, now that the leave is cancelled.

		The student was in the hostel after all, so the reduction must go whoever cancelled
		the leave. Their right to cancel the leave has been checked; the rules for cancelling
		a reduction directly (a student may not once it has started, office staff may not at
		all) do not apply to one that exists only because of this leave, so the system
		withdraws it. When that is impossible (the month is already billed) the people who
		handle reductions are told, so the bill can be corrected by hand.
		"""
		if not self.mess_reduction:
			return
		try:
			from hostel360.hostel_mess.services import cancel_mess_reduction
		except ImportError:
			return
		reduction = frappe.db.get_value(REDUCTION, self.mess_reduction, ["status", "home_visit_leave"], as_dict=True)
		if not reduction or reduction.status not in REDUCTION_OPEN_STATUSES:
			return
		withdrawn_by = "Administrator" if reduction.home_visit_leave == self.name else user

		frappe.db.savepoint("leave_cancel_reduction")
		try:
			cancel_mess_reduction(self.mess_reduction, withdrawn_by)
		except (frappe.ValidationError, frappe.PermissionError) as e:
			frappe.db.rollback(save_point="leave_cancel_reduction")
			frappe.clear_last_message()
			reason = str(e)
			self.add_comment(
				"Info",
				_("Mess reduction {0} could not be cancelled automatically: {1}").format(self.mess_reduction, reason),
			)
			self.alert_reduction_handlers(reason)
			return
		self.add_comment("Info", _("Mess reduction {0} was cancelled with the leave.").format(self.mess_reduction))

	def alert_reduction_handlers(self, reason: str):
		"""Desk notification to the people who decide mess reductions for this student and to
		the manager of the mess, who corrects the bill."""
		from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

		users = set(get_warden_approvers(self.hostel, self.student))
		try:
			from hostel360.hostel_mess.utils import get_reduction_decider_roles

			roles = set(get_reduction_decider_roles())
		except ImportError:
			roles = set()
		# Deputy wardens are already included for this hostel only; IT administrators are not
		# the people who correct a mess bill.
		users.update(get_role_users(roles - {DEPUTY_WARDEN_ROLE, "System Manager"}))
		mess = frappe.db.get_value(REDUCTION, self.mess_reduction, "mess")
		manager = mess and frappe.db.get_value("Hostel Mess", mess, "mess_manager")
		users.update([manager] if manager else get_role_users({MESS_MANAGER_ROLE}))
		if not users:
			return
		subject = frappe.utils.escape_html(
			_("Leave {0} of {1} was cancelled, but its mess reduction {2} is still in force.").format(
				self.name, self.student_name or self.student, self.mess_reduction
			)
		)
		enqueue_create_notification(
			sorted(users),
			{
				"type": "Alert",
				"document_type": REDUCTION,
				"document_name": self.mess_reduction,
				"subject": subject,
				"email_content": f"{subject}<br><br>{frappe.utils.escape_html(reason)}",
				"from_user": frappe.session.user,
			},
		)

	def record_check_out(self, user: str, checked_out_on=None):
		if self.status != "Approved":
			frappe.throw(_("Only approved leaves can be checked out (this one is {0}).").format(_(self.status)))
		at = movement_time(checked_out_on)
		opens_at = check_out_opens_at(self.from_datetime)
		if at < opens_at:
			frappe.throw(
				_("Check-out for this leave opens at {0}, {1} minutes before the leave starts.").format(
					frappe.utils.format_datetime(opens_at), EARLY_CHECK_OUT_MINUTES
				),
				title=_("Leave Not Started"),
			)
		if at >= get_datetime(self.to_datetime):
			frappe.throw(
				_("This leave ended at {0}. The student needs a new leave to go out.").format(
					frappe.utils.format_datetime(self.to_datetime)
				),
				title=_("Leave Ended"),
			)
		self.checked_out_on = at
		self.status = "Checked Out"
		self.save_status()
		self.notify_guardians("leave_checked_out")

	def record_return(self, user: str, returned_on=None):
		if self.status not in RETURNABLE_STATUSES:
			frappe.throw(_("A return cannot be recorded for a leave that is {0}.").format(_(self.status)))
		returned_on = movement_time(returned_on)
		if self.checked_out_on and returned_on < get_datetime(self.checked_out_on):
			frappe.throw(_("Return time cannot be before the check-out time."))
		if not self.checked_out_on and returned_on < check_out_opens_at(self.from_datetime):
			frappe.throw(
				_("This leave has not started yet. Cancel it instead if the student is not going."),
				title=_("Leave Not Started"),
			)
		self.returned_on = returned_on
		self.status = "Returned"
		self.save_status()
		self.notify_guardians("leave_returned")

	def mark_overdue(self):
		self.status = "Overdue"
		self.overdue_since = now_datetime()
		self.save_status()
		notifications.send(
			"leave_overdue",
			notifications.for_users(get_warden_approvers(self.hostel, self.student))
			+ notifications.for_guardians(self.get_all_guardians()),
			notifications.leave_context(self),
			self,
		)

	def get_all_guardians(self) -> list[str]:
		from hostel360.utils.context import get_guardians

		return get_guardians(self.student)

	# ------------------------------------------------------------------ desk actions

	def check_staff_write(self):
		user = frappe.session.user
		if not is_staff_user(user) or not frappe.has_permission(self.doctype, "write", doc=self, user=user):
			frappe.throw(_("You are not permitted to update this leave."), frappe.PermissionError)

	def check_movement_access(self, user: str) -> bool:
		"""May the user record the student leaving or coming back? Returns True for gate staff.

		Hostel staff who may edit the leave can, and so can gate staff (Hostel Security), who
		only read leaves, for students of the hostels and blocks they are posted to. Gate staff
		record the server's time and nothing else; a wrong time is for the warden to correct.
		"""
		if user == "Administrator":
			return False
		# Not self.has_permission(): an earlier action on this object leaves ignore_permissions set.
		if is_staff_user(user) and frappe.has_permission(self.doctype, "write", doc=self, user=user):
			return False
		if (
			SECURITY_ROLE in frappe.get_roles(user)
			and frappe.has_permission(self.doctype, "read", doc=self, user=user)
			and scope_covers_record(get_staff_scope(user), self.hostel, self.student)
		):
			return True
		frappe.throw(_("You are not permitted to record check-out or return for this leave."), frappe.PermissionError)

	def record_movement(self, action: str, at: str | None = None):
		"""Check-out or return started by the signed-in user, on the stored leave."""
		if action not in MOVEMENT_ACTIONS:
			frappe.throw(_("Unknown action."))
		user = frappe.session.user
		if self.check_movement_access(user) and at:
			frappe.throw(
				_("Gate staff record the current time only. Please ask the warden to correct a time."),
				frappe.PermissionError,
			)
		if action == "check_out":
			self.record_check_out(user, at)
		else:
			self.record_return(user, at)

	# The form sends its own copy of the leave with these calls; every action works on the
	# stored record instead and then refreshes the form's copy from it.

	@frappe.whitelist(methods=["POST"])
	def warden_approve(self, remarks: str | None = None):
		load_for_action(self).apply_warden_decision(True, remarks, frappe.session.user)
		self.reload()

	@frappe.whitelist(methods=["POST"])
	def warden_reject(self, remarks: str | None = None):
		load_for_action(self).apply_warden_decision(False, remarks, frappe.session.user)
		self.reload()

	@frappe.whitelist(methods=["POST"])
	def mark_checked_out(self, checked_out_on: str | None = None):
		load_for_action(self).record_movement("check_out", checked_out_on)
		self.reload()

	@frappe.whitelist(methods=["POST"])
	def mark_returned(self, returned_on: str | None = None):
		load_for_action(self).record_movement("return", returned_on)
		self.reload()

	@frappe.whitelist(methods=["POST"])
	def cancel_leave(self):
		leave = load_for_action(self)
		leave.check_staff_write()
		leave.cancel_by(frappe.session.user)
		self.reload()

	# ------------------------------------------------------------------ notifications

	def notify_submitted(self):
		context = notifications.leave_context(self)
		notifications.send("leave_submitted", notifications.for_student(self.student), context, self)
		if self.status == "Pending Parent Approval":
			notifications.send(
				"leave_parent_approval_required",
				notifications.for_guardians(self.get_approver_guardians()),
				context,
				self,
			)
		elif self.status == "Pending Warden Approval":
			self.notify_wardens()

	def notify_wardens(self):
		notifications.send(
			"leave_warden_approval_required",
			notifications.for_users(get_warden_approvers(self.hostel, self.student)),
			notifications.leave_context(self),
			self,
		)

	def notify_decided(self):
		notifications.send(
			"leave_decided",
			notifications.for_student(self.student, include_guardians=True),
			notifications.leave_context(self),
			self,
		)

	def notify_guardians(self, event: str):
		"""Parents hear when their ward leaves the hostel and when they are back."""
		notifications.send(
			event,
			notifications.for_student(self.student, include_student=False, include_guardians=True),
			notifications.leave_context(self),
			self,
		)


def check_out_opens_at(from_datetime):
	return add_to_date(get_datetime(from_datetime), minutes=-EARLY_CHECK_OUT_MINUTES)


def movement_time(value=None):
	"""The time a check-out or return happened: now, or a time typed in that is not ahead of now."""
	now = now_datetime()
	if not value:
		return now
	at = get_datetime(value)
	if at > add_to_date(now, minutes=FUTURE_TOLERANCE_MINUTES):
		frappe.throw(_("The time cannot be in the future."))
	return at


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def guardian_query(doctype, txt, searchfield, start, page_len, filters):
	"""Parent picker on the leave form: the student's guardians who may approve leave."""
	student = (filters or {}).get("student")
	if not student:
		return []
	frappe.has_permission("Hostel Student", "read", student, throw=True)
	rows = frappe.get_all(
		"Hostel Student Guardian",
		filters={"parent": student, "parenttype": "Hostel Student", "can_approve_leave": 1},
		fields=["guardian", "guardian_name", "relation"],
		order_by="is_primary desc, idx asc",
		ignore_permissions=True,
	)
	txt = (txt or "").lower()
	matches = [
		(r.guardian, r.guardian_name, r.relation)
		for r in rows
		if not txt or txt in (r.guardian or "").lower() or txt in (r.guardian_name or "").lower()
	]
	start, page_len = frappe.utils.cint(start), frappe.utils.cint(page_len) or 20
	return matches[start : start + page_len]
