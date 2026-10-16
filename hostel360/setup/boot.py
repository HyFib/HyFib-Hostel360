import frappe


def extend_bootinfo(bootinfo) -> None:
	"""Expose institution branding to the desk (used by desk scripts and print previews)."""
	if frappe.session.user == "Guest":
		return
	try:
		settings = frappe.get_cached_doc("Hostel Settings")
	except frappe.DoesNotExistError:
		return
	bootinfo.hostel360 = {
		"institution_name": settings.get("institution_name"),
		"institution_short_name": settings.get("institution_short_name"),
		"current_academic_year": settings.get("current_academic_year"),
		"support_phone": settings.get("support_phone"),
		"support_email": settings.get("support_email"),
	}
