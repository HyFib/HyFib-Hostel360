# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, now_datetime

from hostel360.hostel_operations.constants import (
	OPEN_STATUSES,
	STATUS_ACKNOWLEDGED,
	STATUS_RESOLVED,
)


class OpsAlert(Document):
	def validate(self):
		from hostel360.hostel_operations.alerts import normalise_key

		now = now_datetime()
		try:
			self.alert_key = normalise_key(self.alert_key or self.title)
		except ValueError:
			frappe.throw(_("An alert needs a key or a title."), frappe.MandatoryError)
		self.first_seen = self.first_seen or now
		self.last_seen = self.last_seen or self.first_seen
		self.occurrence_count = max(cint(self.occurrence_count), 1)

		self.open_key = self.alert_key if self.status in OPEN_STATUSES else None
		if self.open_key:
			self._validate_single_open_alert()

		if self.status == STATUS_ACKNOWLEDGED and not self.acknowledged_on:
			self.acknowledged_on = now
			self.acknowledged_by = self.acknowledged_by or frappe.session.user
		if self.status == STATUS_RESOLVED:
			self.resolved_on = self.resolved_on or now
			self.duration_minutes = max(
				int((get_datetime(self.resolved_on) - get_datetime(self.first_seen)).total_seconds() // 60), 0
			)
		self._validate_details()

	def _validate_single_open_alert(self):
		other = frappe.db.get_value(
			"Ops Alert", {"open_key": self.open_key, "name": ("!=", self.name or "")}, "name"
		)
		if other:
			frappe.throw(
				_("Alert {0} is already open for key {1}. Update that alert instead.").format(
					frappe.bold(other), frappe.bold(self.open_key)
				),
				frappe.UniqueValidationError,
			)

	def _validate_details(self):
		if not self.details:
			return
		try:
			json.loads(self.details)
		except ValueError:
			# Keep free-form text from the desk readable instead of rejecting it.
			self.details = json.dumps({"text": self.details}, indent=1)

	def after_insert(self):
		if self.flags.raised_by_service:
			return
		# Raised by hand from the desk: page people exactly as for an automatic alert.
		from hostel360.hostel_operations import notification
		from hostel360.hostel_operations.settings import get_settings

		settings = get_settings()
		if not notification.notifications_enabled(settings):
			return
		now = now_datetime()
		state = notification.get_alert_state(self.name)
		plan = notification.plan_notification(state, settings, now)
		if plan:
			notification.record_plan(self.name, state, plan, now)
			notification.dispatch(self.name, plan.event, plan.levels)
