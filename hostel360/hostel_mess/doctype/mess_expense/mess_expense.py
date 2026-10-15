# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

from hostel360.hostel_mess.utils import assert_period_open, in_migration


class MessExpense(Document):
	def validate(self):
		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Amount must be greater than zero."))
		if not in_migration() and getdate(self.posting_date) > getdate(nowdate()):
			frappe.throw(_("Expense date cannot be in the future."))
		if self.is_new() or self.has_value_changed("posting_date") or self.has_value_changed("mess"):
			assert_period_open(self.mess, self.posting_date, _("Mess expenses"))

	def before_submit(self):
		assert_period_open(self.mess, self.posting_date, _("Mess expenses"))

	def before_cancel(self):
		assert_period_open(self.mess, self.posting_date, _("Mess expenses"))
