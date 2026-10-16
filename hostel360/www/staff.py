"""/staff: the staff desk's front door, and the route Hostel360 opens at from the apps screen.

Frappe opens an app at one fixed route, but each desk role has its own home (see
``hostel360.setup.landing``), so this page sends a signed-in person on to theirs. Students and
parents go to their portal, and anyone signed out to the sign-in page, which brings them back.
"""

import frappe

from hostel360.setup.landing import landing_route

no_cache = 1
sitemap = 0


def get_context(context):
	user = frappe.session.user
	if user == "Guest":
		location = "/login?redirect-to=/staff"
	elif frappe.get_cached_value("User", user, "user_type") != "System User":
		location = "/hostel"
	else:
		location = landing_route(user)
	frappe.local.flags.redirect_location = location
	# 302, not 301: the destination depends on who is signed in, and a browser caches a 301.
	raise frappe.Redirect(302)
