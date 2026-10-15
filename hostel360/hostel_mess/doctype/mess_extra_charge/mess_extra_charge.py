# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

from hostel360.hostel_mess.permissions import assert_mess_access
from hostel360.hostel_mess.utils import (
	assert_period_open,
	get_mess_for_hostel,
	get_student_hostel,
	in_migration,
)


class MessExtraCharge(Document):
	def validate(self):
		if not frappe.db.exists("Hostel Student", self.student):
			frappe.throw(_("Student {0} does not exist.").format(self.student))
		self.student_name = frappe.db.get_value("Hostel Student", self.student, "full_name")
		if not self.hostel or self.has_value_changed("student"):
			self.hostel = get_student_hostel(self.student)
		if not self.mess:
			self.mess = get_mess_for_hostel(self.hostel)
		if not self.mess:
			frappe.throw(
				_("Select the mess. No mess serves hostel {0}.").format(self.hostel or _("(not allotted)"))
			)
		assert_mess_access(self.mess)

		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Amount must be greater than zero."))
		if not in_migration() and getdate(self.charge_date) > getdate(nowdate()):
			frappe.throw(_("Charge date cannot be in the future."))

		if self.is_new():
			if not in_migration():
				self.status = "Unbilled"
				self.mess_bill = None
		elif (
			self.get_doc_before_save()
			and self.get_doc_before_save().status == "Billed"
			and not in_migration()
		):
			frappe.throw(
				_("This charge is already billed in {0} and cannot be changed.").format(self.mess_bill)
			)

		if self.status == "Unbilled":
			# Unbilled charges go straight into the next bill, so saving one is a change to the books.
			assert_period_open(self.mess, self.charge_date, _("Extra charges"), lock=True)

	def on_trash(self):
		if self.status == "Billed":
			frappe.throw(_("A billed extra charge cannot be deleted."))
		assert_period_open(self.mess, self.charge_date, _("Extra charges"), lock=True)
