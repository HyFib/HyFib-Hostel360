import frappe


def extend_bootinfo(bootinfo) -> None:
	"""Expose institution branding to the desk (used by desk scripts and print previews).

	``demo`` is set only on a demonstration site (``hostel360.demo``); the desk bundle then
	shows the demonstration banner on every page.
	"""
	if frappe.session.user == "Guest":
		return
	from hostel360.demo import desk_boot

	info = {"demo": desk_boot()}
	try:
		settings = frappe.get_cached_doc("Hostel Settings")
	except frappe.DoesNotExistError:
		settings = None
	if settings:
		info.update(
			institution_name=settings.get("institution_name"),
			institution_short_name=settings.get("institution_short_name"),
			current_academic_year=settings.get("current_academic_year"),
			support_phone=settings.get("support_phone"),
			support_email=settings.get("support_email"),
		)
	bootinfo.hostel360 = info
