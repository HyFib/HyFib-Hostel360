"""Desk actions Frappe offers to every user but lets only System Managers complete."""

from typing import Any

import frappe
from frappe.desk.doctype.list_view_settings import list_view_settings


@frappe.whitelist()
def save_listview_settings(
	doctype: str, listview_settings: str | dict[str, Any], removed_listview_fields: str | list[str]
):
	"""Save a list's settings, or for staff who may not, keep a resized column for the page only.

	List View Settings are one record per DocType, shared by every user, and only System
	Managers may change them. Frappe v16 also saves them when anyone drags a list column wider,
	so other staff got a "Not permitted" message for resizing a column. For them the width is
	kept on the page they are on and nothing is stored, so one person cannot change a list for
	everybody.
	"""
	if frappe.has_permission("List View Settings", "write"):
		return list_view_settings.save_listview_settings(doctype, listview_settings, removed_listview_fields)
	return {"listview_settings": frappe.parse_json(listview_settings) or {}}
