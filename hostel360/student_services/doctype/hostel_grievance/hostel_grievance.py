# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_to_date, cint, flt, get_datetime, now_datetime, time_diff_in_hours

from hostel360.setup.roles import GRIEVANCE_HANDLER_ROLES, MESS_MANAGER_ROLE
from hostel360.student_services import notifications
from hostel360.student_services.permissions import can_see_confidential
from hostel360.student_services.utils import (
	assert_unchanged,
	get_role_users,
	get_senior_wardens,
	get_setting,
	get_student,
	get_warden_approvers,
	in_migration,
	is_grievance_handler,
	is_staff_user,
	load_for_action,
	recheck_scope,
	stars_to_rating,
	student_hostel,
	user_display_name,
)

RAGGING_CATEGORY = "Ragging or Harassment"
MESS_CATEGORY = "Mess and Food"
OPEN_STATUSES = ("Open", "In Progress", "Escalated", "Reopened")
FINISHED_STATUSES = ("Resolved", "Closed")
REOPEN_WINDOW_DAYS = 7
# The first escalation reaches the hostel's deputy wardens as well as the Warden and hostel
# administrators; later ones go to the Warden and administrators only. After the third, more
# reminders add nothing but noise.
MAX_ESCALATION_LEVEL = 3
MAX_MESSAGE_LENGTH = 5000

# Taken from the student when the grievance is raised; never edited after.
IDENTITY_FIELDS = ("student", "hostel", "room", "raised_by_user")
# Changed only by the actions (assign, update, resolve, close, reopen, rate, escalate), which
# save through save_changes(). An ordinary save may still correct the subject, description,
# category and priority.
WORKFLOW_FIELDS = (
	"status",
	"assigned_to",
	"assigned_on",
	"sla_due_by",
	"escalation_level",
	"escalated_on",
	"sla_breached",
	"reopen_count",
	"resolution",
	"resolved_by",
	"resolved_on",
	"resolution_hours",
	"closed_on",
	"student_rating",
	"student_feedback",
	"updates",
)


class HostelGrievance(Document):
	def validate(self):
		if self.is_new():
			self.set_student_details()
		else:
			self.validate_protected_fields()
		self.apply_category_rules()
		self.subject = (self.subject or "").strip()
		if self.is_new():
			self.set_initial_state()
		recheck_scope(self)

	def after_insert(self):
		self.notify_received()

	def set_student_details(self):
		student = get_student(self.student)
		self.student_name = student.full_name
		if not (in_migration() and self.hostel):
			# A student who has left can still complain; the grievance stays with their last hostel.
			self.hostel = student_hostel(student)
			self.room = student.get("current_room")
		self.raised_by_user = self.raised_by_user or frappe.session.user

	def validate_protected_fields(self):
		if in_migration():
			return
		assert_unchanged(self, IDENTITY_FIELDS, _("{0} cannot be changed on a grievance that has been raised."))
		if not self.flags.via_controller:
			assert_unchanged(
				self,
				WORKFLOW_FIELDS,
				_("{0} can only be changed with the Assign, Add Update, Resolve and Close actions."),
			)
		self.validate_confidentiality_kept()

	def validate_confidentiality_kept(self):
		"""Only those who may see confidential grievances may make one visible to other staff."""
		if can_see_confidential():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if (cint(before.is_confidential) and not cint(self.is_confidential)) or (
			before.category == RAGGING_CATEGORY and self.category != RAGGING_CATEGORY
		):
			frappe.throw(
				_("Only the Warden or a Hostel Administrator can remove the confidentiality of a grievance."),
				frappe.PermissionError,
				title=_("Not Allowed"),
			)

	def apply_category_rules(self):
		# Ragging and harassment complaints are handled by the Warden in confidence (UGC regulations).
		if self.category == RAGGING_CATEGORY:
			self.is_confidential = 1
			self.priority = "Urgent"

	def set_initial_state(self):
		if in_migration() and self.status:
			return
		# A new grievance has no handling history, whatever the request said.
		for fieldname in WORKFLOW_FIELDS:
			self.set(fieldname, [] if fieldname == "updates" else None)
		self.status = "Open"
		self.escalation_level = 0
		self.sla_due_by = self.compute_sla_due_by()
		self.append_update(_("Grievance registered."), user=self.raised_by_user)
		if self.category == RAGGING_CATEGORY:
			self.status = "Escalated"
			self.escalation_level = 1
			self.escalated_on = now_datetime()
			self.append_update(
				_("Referred to the Warden for immediate action."),
				status_change="Open → Escalated",
				user="Administrator",
			)

	def compute_sla_due_by(self, start=None):
		key = "grievance_urgent_escalation_hours" if self.priority == "Urgent" else "grievance_escalation_hours"
		hours = max(cint(get_setting(key)), 1)
		return add_to_date(start or now_datetime(), hours=hours)

	# ------------------------------------------------------------------ timeline

	def append_update(
		self,
		message: str,
		is_internal: int = 0,
		status_change: str | None = None,
		user: str | None = None,
	):
		self.append(
			"updates",
			{
				"update_on": now_datetime(),
				"update_by": user or frappe.session.user,
				"message": message,
				"is_internal": cint(is_internal),
				"status_change": status_change,
			},
		)

	def set_status(self, status: str, message: str, user: str, is_internal: int = 0):
		change = f"{self.status} → {status}" if status != self.status else None
		self.status = status
		self.append_update(message, is_internal=is_internal, status_change=change, user=user)

	def save_changes(self):
		self.flags.ignore_permissions = True
		self.flags.via_controller = True
		try:
			self.save()
		finally:
			self.flags.via_controller = False

	def public_updates(self) -> list[dict]:
		timeline = []
		for row in self.updates:
			if row.is_internal:
				continue
			# System entries (registration, escalation) are recorded as Administrator.
			by = _("Hostel Office") if row.update_by == "Administrator" else user_display_name(row.update_by)
			timeline.append(
				{
					"on": row.update_on,
					"by": by,
					"is_staff": is_staff_user(row.update_by) if row.update_by else False,
					"message": row.message,
					"status_change": row.status_change,
				}
			)
		return timeline

	# ------------------------------------------------------------------ staff actions

	def check_staff_action(self, user: str):
		"""Staff with write access, or the grievance handler it is assigned to."""
		if not is_staff_user(user):
			frappe.throw(_("Only hostel staff can do this."), frappe.PermissionError)
		if self.assigned_to == user and is_grievance_handler(user):
			return
		# Not self.has_permission(): an earlier action on this object leaves ignore_permissions set.
		if frappe.has_permission(self.doctype, "write", doc=self, user=user):
			return
		frappe.throw(_("You are not permitted to update this grievance."), frappe.PermissionError)

	def assign_to_user(self, assignee: str, user: str, note: str | None = None):
		self.check_staff_action(user)
		if self.status in FINISHED_STATUSES:
			frappe.throw(_("A {0} grievance cannot be assigned.").format(_(self.status).lower()))
		if not frappe.db.get_value("User", {"name": assignee, "enabled": 1, "user_type": "System User"}):
			frappe.throw(_("{0} is not an active staff user.").format(assignee))
		if not is_grievance_handler(assignee):
			frappe.throw(
				_("{0} cannot handle grievances (only hostel and mess staff can).").format(
					user_display_name(assignee)
				)
			)

		self.assigned_to = assignee
		self.assigned_on = now_datetime()
		new_status = "In Progress" if self.status in ("Open", "Reopened") else self.status
		self.set_status(new_status, _("Assigned to {0}.").format(user_display_name(assignee)), user)
		if note:
			self.append_update(note, is_internal=1, user=user)
		self.save_changes()

		context = notifications.grievance_context(self, note)
		notifications.send("grievance_assigned", notifications.for_users([assignee]), context, self)
		notifications.send("grievance_updated", notifications.for_student(self.student), context, self)

	def add_staff_update(self, message: str, user: str, is_internal: int = 0, status: str | None = None):
		self.check_staff_action(user)
		message = clean_message(message)
		if self.status == "Closed":
			frappe.throw(_("This grievance is closed."))
		if status and status not in ("In Progress",):
			frappe.throw(_("Use Resolve or Close to finish a grievance."))
		if status and status != self.status:
			self.set_status(status, message, user, is_internal=is_internal)
		else:
			self.append_update(message, is_internal=is_internal, user=user)
		self.save_changes()
		if not cint(is_internal):
			notifications.send(
				"grievance_updated",
				notifications.for_student(self.student),
				notifications.grievance_context(self, message),
				self,
			)

	def resolve_by(self, resolution: str, user: str):
		self.check_staff_action(user)
		resolution = clean_message(resolution)
		if self.status not in OPEN_STATUSES:
			frappe.throw(_("Only open grievances can be resolved (this one is {0}).").format(_(self.status)))
		now = now_datetime()
		self.resolution = resolution
		self.resolved_by = user
		self.resolved_on = now
		self.resolution_hours = flt(time_diff_in_hours(now, self.creation), 1)
		if self.sla_due_by and now > get_datetime(self.sla_due_by):
			self.sla_breached = 1
		self.set_status("Resolved", resolution, user)
		self.save_changes()
		notifications.send(
			"grievance_resolved",
			notifications.for_student(self.student),
			notifications.grievance_context(self, resolution),
			self,
		)

	def close_by(self, user: str, remarks: str | None = None):
		self.check_staff_action(user)
		if self.status == "Closed":
			return
		remarks = (remarks or "").strip()
		if self.status != "Resolved" and not remarks:
			frappe.throw(_("Please say why this grievance is being closed without a resolution."))
		self.closed_on = now_datetime()
		self.set_status("Closed", remarks or _("Grievance closed."), user)
		self.save_changes()
		notifications.send(
			"grievance_updated",
			notifications.for_student(self.student),
			notifications.grievance_context(self, remarks),
			self,
		)

	# ------------------------------------------------------------------ student actions

	def reopen_window_open(self) -> bool:
		if self.status not in FINISHED_STATUSES or not self.resolved_on:
			return False
		return now_datetime() <= add_days(get_datetime(self.resolved_on), REOPEN_WINDOW_DAYS)

	def reopen_by(self, reason: str, user: str):
		reason = clean_message(reason)
		if self.status not in FINISHED_STATUSES or not self.resolved_on:
			frappe.throw(_("Only a resolved grievance can be reopened."))
		if not self.reopen_window_open():
			frappe.throw(
				_("A grievance can be reopened within {0} days of its resolution. Please raise a new one.").format(
					REOPEN_WINDOW_DAYS
				)
			)
		self.reopen_count = cint(self.reopen_count) + 1
		self.closed_on = None
		self.sla_due_by = self.compute_sla_due_by()
		self.set_status("Reopened", reason, user)
		self.save_changes()
		notifications.send(
			"grievance_updated",
			notifications.for_users(self.get_handlers()),
			notifications.grievance_context(self, reason),
			self,
		)

	def rate_by(self, rating: int, feedback: str | None, user: str):
		if self.status not in FINISHED_STATUSES:
			frappe.throw(_("You can rate a grievance once it is resolved."))
		if cint(rating) < 1 or cint(rating) > 5:
			frappe.throw(_("Please choose a rating between 1 and 5 stars."))
		self.student_rating = stars_to_rating(rating)
		self.student_feedback = (feedback or "").strip()[:MAX_MESSAGE_LENGTH] or None
		self.save_changes()

	def comment_by(self, comment: str, user: str):
		comment = clean_message(comment)
		if self.status == "Closed":
			frappe.throw(_("This grievance is closed. Please raise a new one if the problem continues."))
		self.append_update(comment, user=user)
		self.save_changes()
		notifications.send(
			"grievance_updated",
			notifications.for_users(self.get_handlers()),
			notifications.grievance_context(self, comment),
			self,
		)

	# ------------------------------------------------------------------ escalation

	def escalate(self):
		level = min(cint(self.escalation_level) + 1, MAX_ESCALATION_LEVEL)
		now = now_datetime()
		self.escalation_level = level
		self.escalated_on = now
		self.sla_breached = 1
		self.sla_due_by = self.compute_sla_due_by(now)
		self.set_status(
			"Escalated",
			_("Not resolved in time; escalated to level {0}.").format(level),
			"Administrator",
		)
		self.save_changes()
		notifications.send(
			"grievance_escalated",
			notifications.for_users(self.get_escalation_recipients(level)),
			notifications.grievance_context(self),
			self,
		)

	def get_escalation_recipients(self, level: int) -> list[str]:
		users = set(get_senior_wardens())
		if level <= 1 and not self.is_confidential:
			users.update(get_warden_approvers(self.hostel, self.student))
		if self.assigned_to:
			users.add(self.assigned_to)
		return sorted(users)

	def get_handlers(self) -> list[str]:
		"""Staff who should hear about this grievance."""
		if self.assigned_to:
			return [self.assigned_to]
		if self.is_confidential:
			return get_senior_wardens()
		users = set(get_warden_approvers(self.hostel, self.student))
		if self.category == MESS_CATEGORY:
			users.update(get_role_users({MESS_MANAGER_ROLE}))
		return sorted(users)

	def notify_received(self):
		context = notifications.grievance_context(self)
		notifications.send("grievance_received", notifications.for_student(self.student), context, self)
		notifications.send("grievance_received", notifications.for_users(self.get_handlers()), context, self)
		if self.status == "Escalated":
			notifications.send(
				"grievance_escalated",
				notifications.for_users(get_senior_wardens()),
				context,
				self,
			)

	# ------------------------------------------------------------------ desk actions
	# The form sends its own copy of the grievance with these calls; each action works on the
	# stored record (whose assignee and confidentiality decide who may act) and then refreshes
	# the form's copy from it.

	@frappe.whitelist(methods=["POST"])
	def assign(self, user: str, note: str | None = None):
		load_for_action(self).assign_to_user(user, frappe.session.user, note)
		self.reload()

	@frappe.whitelist(methods=["POST"])
	def add_update(self, message: str, is_internal: int = 0, status: str | None = None):
		load_for_action(self).add_staff_update(
			message, frappe.session.user, is_internal=cint(is_internal), status=status
		)
		self.reload()

	@frappe.whitelist(methods=["POST"])
	def resolve(self, resolution: str):
		load_for_action(self).resolve_by(resolution, frappe.session.user)
		self.reload()

	@frappe.whitelist(methods=["POST"])
	def close(self, remarks: str | None = None):
		load_for_action(self).close_by(frappe.session.user, remarks)
		self.reload()


def clean_message(message: str | None) -> str:
	message = (message or "").strip()
	if not message:
		frappe.throw(_("Please enter a message."))
	if len(message) > MAX_MESSAGE_LENGTH:
		frappe.throw(_("Please keep the message under {0} characters.").format(MAX_MESSAGE_LENGTH))
	return message


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def staff_user_query(doctype, txt, searchfield, start, page_len, filters):
	"""Assignee picker: enabled desk users who may handle grievances."""
	from frappe.query_builder import DocType

	# Only those who can assign grievances need the list of staff logins; students and parents
	# are signed-in users too.
	if not is_grievance_handler(frappe.session.user):
		frappe.throw(_("Only hostel staff can do this."), frappe.PermissionError)

	user, has_role = DocType("User"), DocType("Has Role")
	like = f"%{txt}%"
	query = (
		frappe.qb.from_(user)
		.join(has_role)
		.on((has_role.parent == user.name) & (has_role.parenttype == "User"))
		.select(user.name, user.full_name)
		.distinct()
		.where(user.enabled == 1)
		.where(user.user_type == "System User")
		.where(user.name.notin(["Administrator", "Guest"]))
		.where(has_role.role.isin(sorted(GRIEVANCE_HANDLER_ROLES)))
		.where((user.name.like(like)) | (user.full_name.like(like)))
		.orderby(user.full_name)
		.limit(page_len)
		.offset(start)
	)
	return query.run()
