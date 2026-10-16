"""Page context for the student and parent portal at /hostel.

``hostel.html`` is produced by the frontend build. It carries the CSRF token in a meta tag
and the boot data in a ``<script type="application/json">`` block, so the page needs no
inline script (the portal runs under a strict Content Security Policy). Everything else
the app loads through ``hostel360.api.portal.get_session``.

Desk users are sent to their desk home (``/staff``): the portal only works for students and
parents, and a staff member landing here by habit should not see a sign-in screen meant for
residents.
"""

import json

import frappe
from frappe import _
from markupsafe import Markup

no_cache = 1
sitemap = 0

DESK_HOME = "/staff"

# Characters that could end the <script> block or open markup inside it. JSON allows these
# \u escapes, so JSON.parse returns the original text.
HTML_UNSAFE_JSON = (
	("<", "\\u003c"),
	(">", "\\u003e"),
	("&", "\\u0026"),
	("\u2028", "\\u2028"),
	("\u2029", "\\u2029"),
)


def get_context(context):
	context.no_cache = 1
	user = frappe.session.user
	logged_in = bool(user) and user != "Guest"

	if logged_in and is_desk_user(user):
		context.redirect_to = DESK_HOME
		frappe.local.flags.redirect_location = DESK_HOME
		# 302, not 301: a permanent redirect would be cached by the browser and keep sending
		# the next person who uses this device (perhaps a student) to the desk.
		raise frappe.Redirect(302)

	if logged_in:
		from frappe.sessions import get_csrf_token

		context.csrf_token = get_csrf_token()
	else:
		context.csrf_token = ""

	boot = get_boot(user if logged_in else None)
	context.boot_json = html_safe_json(boot)
	context.lang = boot["lang"]
	context.title = boot.get("institution_short_name") or _("Hostel Portal")
	return context


def is_desk_user(user: str) -> bool:
	if user == "Administrator":
		return True
	return frappe.get_cached_value("User", user, "user_type") == "System User"


def get_boot(user: str | None) -> dict:
	from hostel360.api.portal import get_branding
	from hostel360.utils.context import get_portal_identity

	branding = get_branding()
	kind = get_portal_identity(user).kind if user else None
	return {
		"site_name": frappe.local.site,
		"institution_name": branding.get("institution_name"),
		"institution_short_name": branding.get("institution_short_name"),
		"logo": branding.get("logo"),
		# The Help page is reachable before sign-in; it must be able to show the hostel office.
		"office_phone": branding.get("office_phone"),
		"office_email": branding.get("office_email"),
		"support_phone": branding.get("support_phone"),
		"support_email": branding.get("support_email"),
		"is_logged_in": bool(user),
		"kind": kind,
		"lang": getattr(frappe.local, "lang", None) or "en",
		# Whether the sign-in page offers e-mail codes: a site-wide setting, nothing about anyone.
		"otp_email": email_codes_offered(),
		# Only on a demonstration site: the banner, and the published demo sign-ins (all
		# fictitious) for the sign-in page's helper. None everywhere else.
		"demo": portal_demo(),
	}


def portal_demo() -> dict | None:
	from hostel360.demo import portal_boot

	return portal_boot()


def email_codes_offered() -> bool:
	from hostel360.auth.otp import email_codes_enabled

	try:
		return email_codes_enabled()
	except Exception:
		# The page must open even when Messaging Settings cannot be read; SMS still works.
		frappe.logger("hostel360").warning("Could not read the sign-in code settings", exc_info=True)
		return False


def html_safe_json(value) -> Markup:
	"""JSON that is safe to place verbatim inside an HTML <script> element."""
	text = json.dumps(value, separators=(",", ":"), default=str)
	for raw, escaped in HTML_UNSAFE_JSON:
		text = text.replace(raw, escaped)
	# Already escaped for this context; Markup keeps Jinja from escaping it a second time.
	return Markup(text)  # noqa: S704 - HTML-significant characters were escaped above
