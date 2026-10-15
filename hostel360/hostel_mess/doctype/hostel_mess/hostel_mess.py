# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from hostel360.hostel_mess.utils import in_migration
from hostel360.setup.roles import ADMIN_ROLE, MESS_MANAGER_ROLE


class HostelMess(Document):
	def validate(self):
		self.mess_name = (self.mess_name or "").strip()
		self.validate_hostels()
		self.validate_manager()

	def validate_hostels(self):
		hostels = [row.hostel for row in self.hostels if row.hostel]
		duplicates = {h for h in hostels if hostels.count(h) > 1}
		if duplicates:
			frappe.throw(_("Hostel {0} is listed more than once.").format(", ".join(sorted(duplicates))))
		if not hostels:
			frappe.throw(_("Select the hostels this mess serves."))
		if not self.is_active:
			return

		# A hostel's residents are billed by exactly one mess.
		clashes = frappe.db.sql(
			"""
			select h.hostel, m.name
			from `tabHostel Mess Hostel` h
			inner join `tabHostel Mess` m on m.name = h.parent
			where h.parenttype = 'Hostel Mess' and h.hostel in %(hostels)s
				and m.is_active = 1 and m.name != %(name)s
			""",
			{"hostels": tuple(hostels), "name": self.name or ""},
			as_dict=True,
		)
		if clashes:
			frappe.throw(
				"<br>".join(
					_("Hostel {0} is already served by {1}.").format(
						frappe.bold(row.hostel), frappe.bold(row.name)
					)
					for row in clashes
				),
				title=_("Hostel Already Assigned"),
			)

	def validate_manager(self):
		if not self.mess_manager or in_migration():
			return
		roles = set(frappe.get_roles(self.mess_manager))
		if not roles.intersection({MESS_MANAGER_ROLE, ADMIN_ROLE, "System Manager"}):
			frappe.throw(
				_("{0} does not have the {1} role.").format(
					frappe.bold(self.mess_manager), _(MESS_MANAGER_ROLE)
				)
			)
