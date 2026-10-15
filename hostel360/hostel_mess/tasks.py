"""Scheduled jobs of the Hostel Mess module (see hooks.py)."""

import frappe
from frappe.utils import cint, flt, nowdate

from hostel360.hostel_mess.stock import get_stock_levels, suggest_reorder_qty
from hostel360.hostel_mess.utils import (
	get_mess_settings,
	get_role_holders,
	send_notification,
	user_recipients,
)
from hostel360.setup.roles import MESS_MANAGER_ROLE


def send_low_stock_digest():
	"""Weekly: tell each mess manager which items are at or below their reorder level."""
	if not cint(get_mess_settings().low_stock_alert):
		return
	for mess in frappe.get_all("Hostel Mess", filters={"is_active": 1}, fields=["name", "mess_manager"]):
		try:
			send_mess_low_stock(mess.name, mess.mess_manager)
		except Exception:
			frappe.log_error(
				title=f"Low stock digest failed for {mess.name}",
				reference_doctype="Hostel Mess",
				reference_name=mess.name,
			)


def send_mess_low_stock(mess: str, mess_manager: str | None = None) -> list[dict]:
	low = [level for level in get_stock_levels(mess) if level.below_reorder and level.is_active]
	if not low:
		return []
	items = [
		{
			"item": level.item,
			"local_name": level.local_name,
			"qty": flt(level.qty, 3),
			"uom": level.uom,
			"reorder_level": flt(level.reorder_level, 3),
			"suggested_qty": suggest_reorder_qty(level),
		}
		for level in low
	]
	users = [mess_manager] if mess_manager else get_role_holders(MESS_MANAGER_ROLE)
	send_notification(
		"mess_low_stock",
		user_recipients(users),
		{
			"mess": mess,
			"date": nowdate(),
			"item_count": len(items),
			"items": items,
			"items_summary": ", ".join(f"{i['item']} ({i['qty']:g} {i['uom']})" for i in items),
		},
		reference_doctype="Hostel Mess",
		reference_name=mess,
	)
	return items
