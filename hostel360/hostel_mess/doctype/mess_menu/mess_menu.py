# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, date_diff, format_date, getdate, now_datetime

from hostel360.hostel_mess.permissions import assert_mess_access
from hostel360.hostel_mess.utils import (
	MEAL_ORDER,
	WEEKDAYS,
	get_mess_student_users,
	get_week_start,
	send_notification,
	user_recipients,
)


class MessMenu(Document):
	def before_insert(self):
		# The name is built from the date, so check it before naming produces an odd name.
		self.validate_week()
		self.validate_unique_week()

	def validate(self):
		assert_mess_access(self.mess, doctype=self.doctype)
		self.validate_week()
		self.validate_unique_week()
		self.clean_items()

	def on_trash(self):
		if self.status == "Published" and "System Manager" not in frappe.get_roles():
			frappe.throw(_("A published menu cannot be deleted. Edit it instead."))

	def validate_week(self):
		start = getdate(self.week_start_date)
		if start.weekday() != 0:
			frappe.throw(
				_("Week Starting must be a Monday. {0} is a {1}; use {2}.").format(
					format_date(start), _(WEEKDAYS[start.weekday()]), format_date(get_week_start(start))
				)
			)
		self.week_end_date = add_days(start, 6)

	def validate_unique_week(self):
		existing = frappe.db.get_value(
			"Mess Menu",
			{"mess": self.mess, "week_start_date": self.week_start_date, "name": ["!=", self.name or ""]},
			"name",
		)
		if existing:
			frappe.throw(
				_("A menu for {0} for the week starting {1} already exists: {2}.").format(
					frappe.bold(self.mess), format_date(self.week_start_date), frappe.bold(existing)
				),
				frappe.DuplicateEntryError,
			)

	def clean_items(self):
		"""Fill the day from the date, drop rows without dishes and order rows by day and meal."""
		start = getdate(self.week_start_date)
		rows, seen = [], set()
		for row in self.items:
			if not (row.dishes or "").strip():
				continue
			row.dishes = row.dishes.strip()
			if not row.menu_date:
				frappe.throw(_("Row {0}: select the date.").format(row.idx))
			offset = date_diff(row.menu_date, start)
			if offset < 0 or offset > 6:
				frappe.throw(
					_("Row {0}: {1} is outside the week {2} to {3}.").format(
						row.idx,
						format_date(row.menu_date),
						format_date(start),
						format_date(self.week_end_date),
					)
				)
			row.day = WEEKDAYS[getdate(row.menu_date).weekday()]
			key = (getdate(row.menu_date), row.meal)
			if key in seen:
				frappe.throw(
					_("{0} on {1} is entered more than once.").format(_(row.meal), format_date(row.menu_date))
				)
			seen.add(key)
			rows.append(row)

		rows.sort(
			key=lambda r: (getdate(r.menu_date), MEAL_ORDER.index(r.meal) if r.meal in MEAL_ORDER else 9)
		)
		for idx, row in enumerate(rows, start=1):
			row.idx = idx
		self.items = rows

	@frappe.whitelist(methods=["POST"])
	def publish(self):
		"""Publish the week's menu and let the residents know (in-app)."""
		self.check_permission("write")
		assert_mess_access(self.mess, doctype=self.doctype)
		if self.status == "Published":
			frappe.throw(_("This menu is already published."))
		if not self.items:
			frappe.throw(_("Add the meals before publishing the menu."))
		self.status = "Published"
		self.published_on = now_datetime()
		self.published_by = frappe.session.user
		self.save()
		self.notify_students()

	def notify_students(self):
		users = get_mess_student_users(self.mess)
		if not users:
			return
		send_notification(
			"menu_published",
			user_recipients(users),
			{
				"mess": self.mess,
				"week_start": str(getdate(self.week_start_date)),
				"week_end": str(getdate(self.week_end_date)),
				"special_meals": cint(sum(1 for row in self.items if row.is_special)),
			},
			reference_doctype=self.doctype,
			reference_name=self.name,
			channels=["In-App"],
		)


@frappe.whitelist()
def get_previous_week_items(mess: str, week_start_date: str) -> list[dict]:
	"""Meals of the latest menu before the given week, moved onto the given week's dates."""
	frappe.has_permission("Mess Menu", "read", throw=True)
	assert_mess_access(mess, doctype="Mess Menu")
	target_start = getdate(week_start_date)
	if target_start.weekday() != 0:
		frappe.throw(_("Week Starting must be a Monday."))

	source = frappe.get_all(
		"Mess Menu",
		filters={"mess": mess, "week_start_date": ["<", target_start]},
		fields=["name", "week_start_date"],
		order_by="week_start_date desc",
		limit=1,
	)
	if not source:
		frappe.throw(_("There is no earlier menu for {0} to copy.").format(frappe.bold(mess)))
	source = source[0]

	rows = frappe.get_all(
		"Mess Menu Item",
		filters={"parent": source.name, "parenttype": "Mess Menu"},
		fields=["menu_date", "meal", "dishes", "is_special"],
		order_by="idx asc",
	)
	items = []
	for row in rows:
		offset = date_diff(row.menu_date, source.week_start_date)
		menu_date = add_days(target_start, offset)
		items.append(
			{
				"menu_date": str(getdate(menu_date)),
				"day": WEEKDAYS[getdate(menu_date).weekday()],
				"meal": row.meal,
				"dishes": row.dishes,
				"is_special": row.is_special,
			}
		)
	return items
