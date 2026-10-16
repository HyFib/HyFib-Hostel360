"""Jinja helpers available in print formats, emails and web templates."""

import frappe
from frappe.utils import flt, money_in_words


def format_inr(amount, precision: int = 2) -> str:
	"""Indian digit grouping: 1234567.5 -> "₹12,34,567.50"."""
	value = flt(amount, precision)
	sign = "-" if value < 0 else ""
	whole, _dot, fraction = f"{abs(value):.{precision}f}".partition(".")
	if len(whole) > 3:
		head, tail = whole[:-3], whole[-3:]
		groups = []
		while len(head) > 2:
			groups.insert(0, head[-2:])
			head = head[:-2]
		if head:
			groups.insert(0, head)
		whole = ",".join([*groups, tail])
	return f"{sign}₹{whole}" + (f".{fraction}" if precision else "")


def amount_in_words_inr(amount) -> str:
	return money_in_words(flt(amount, 2), "INR")


def get_institution_details() -> dict:
	"""Institution name, address and logo from Hostel Settings for letterheads and certificates."""
	settings = frappe.get_cached_doc("Hostel Settings")
	return {
		"institution_name": settings.get("institution_name"),
		"institution_short_name": settings.get("institution_short_name"),
		"address": settings.get("institution_address"),
		"logo": settings.get("institution_logo"),
		"website": settings.get("hostel_website_url"),
		"phone": settings.get("office_phone"),
		"email": settings.get("office_email"),
	}
