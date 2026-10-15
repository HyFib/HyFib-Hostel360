# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import hashlib
import hmac
import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

from hostel360.student_services.utils import assert_student_eligible, get_student, in_migration

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
RATING_FIELDS = ("overall", "food_quality", "cleanliness", "staff_behaviour", "facilities")


class HostelFeedback(Document):
	def validate(self):
		self.period = (self.period or current_period()).strip()
		self.validate_period()
		self.validate_ratings()
		if self.is_new():
			self.set_student_details()

	def validate_period(self):
		if not PERIOD_RE.match(self.period):
			frappe.throw(_("Period must be a month written as YYYY-MM, for example {0}.").format(current_period()))
		if not in_migration() and self.period > current_period():
			frappe.throw(_("Feedback cannot be given for a future month."))

	def validate_ratings(self):
		for fieldname in RATING_FIELDS:
			value = flt(self.get(fieldname))
			if value < 0 or value > 1:
				frappe.throw(_("{0} must be between 1 and 5 stars.").format(_(self.meta.get_label(fieldname))))
		if not flt(self.overall):
			frappe.throw(_("Please give an overall rating."))

	def set_student_details(self):
		if not self.student:
			frappe.throw(_("Please select the student giving this feedback."), frappe.MandatoryError)
		student = get_student(self.student)
		if not in_migration():
			assert_student_eligible(student, _("Feedback"))
		self.hostel = self.hostel if (in_migration() and self.hostel) else student.current_hostel
		self.submission_key = submission_key(self.student, self.feedback_type, self.period)
		if frappe.db.exists("Hostel Feedback", {"submission_key": self.submission_key}):
			frappe.throw(
				_("Feedback on {0} for {1} has already been submitted.").format(_(self.feedback_type), self.period),
				frappe.DuplicateEntryError,
			)
		if self.is_anonymous:
			# Anonymous feedback keeps only a keyed hash of the student, enough to stop a second
			# submission for the same month but not to tell who wrote it.
			self.student = None
			self.student_name = None
		else:
			self.student_name = student.full_name


def current_period() -> str:
	return getdate(nowdate()).strftime("%Y-%m")


def submission_key(student: str, feedback_type: str, period: str) -> str:
	from frappe.utils.password import get_encryption_key

	message = f"{student}|{feedback_type}|{period}".encode()
	return hmac.new(get_encryption_key().encode(), message, hashlib.sha256).hexdigest()


def has_submitted(student: str, feedback_type: str, period: str) -> bool:
	return bool(
		frappe.db.exists("Hostel Feedback", {"submission_key": submission_key(student, feedback_type, period)})
	)
