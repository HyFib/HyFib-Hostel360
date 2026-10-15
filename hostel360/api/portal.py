"""Student and parent portal API (design section 6, "Portal").

Portal users hold no DocType permissions, so this module is the only way a student or a
parent reaches hostel data. Every endpoint follows the same steps:

1. resolve the caller with ``require_portal_identity`` (guests and desk users are refused);
2. pick the student the call is about: a student always acts for themselves, a parent for
   one of their linked wards (``student`` selects which);
3. unless the endpoint is one a new resident needs before validation (session, privacy
   notice, profile, notifications), require that the student is validated by the hostel
   office and that the caller has accepted the current privacy notice;
4. hand over to the module service that owns the business rule.

Read-only lists that are not covered by a service query DocTypes directly, always filtered
on the resolved student and always with an explicit field list.

Writes are rate limited per signed-in user. Frappe's ``rate_limit`` decorator keys on the
client IP or a request parameter, and a whole campus can share one public IP address, so
the limiter here keys on the session user instead.
"""

import json
import re
from functools import wraps
from mimetypes import guess_type
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, getdate, now_datetime, strip_html

from hostel360.setup.roles import PORTAL_ROLES
from hostel360.utils.context import require_portal_identity
from hostel360.utils.validation import mask_mobile

STUDENT = "student"
PARENT = "parent"

STUDENT_DOCTYPE = "Hostel Student"
GUARDIAN_DOCTYPE = "Hostel Guardian"
LEAVE = "Home Visit Leave"
GRIEVANCE = "Hostel Grievance"
FEEDBACK = "Hostel Feedback"
BONAFIDE = "Bonafide Certificate Request"
REDUCTION = "Mess Reduction Application"
PAYMENT = "Hostel Payment"
TRANSACTION = "Hostel Payment Transaction"
NOTIFICATION_LOG = "Notification Log"

PORTAL_SWITCHES = {STUDENT: "student_portal_enabled", PARENT: "parent_portal_enabled"}
PORTAL_HOME = "/hostel"
PHOTO_ENDPOINT = "/api/method/hostel360.api.portal.download_photo"

# Statuses used for the "waiting for a decision" counters on the dashboard. The owning
# modules define them; these are the fallbacks while a module is being installed.
LEAVE_PENDING_STATUSES = ("Pending Parent Approval", "Pending Warden Approval")
GRIEVANCE_OPEN_STATUSES = ("Open", "In Progress", "Escalated", "Reopened")

# Field length limits for free text coming from the portal.
NAME_MAX = 140
SUBJECT_MAX = 140
DESTINATION_MAX = 140
CONTACT_MAX = 20
REMARKS_MAX = 500
REASON_MAX = 1000
DESCRIPTION_MAX = 5000
COMMENT_MAX = 2000
FEEDBACK_COMMENTS_MAX = 2000
FILE_URL_MAX = 255

MAX_INVOICES = 20
MAX_ATTACHMENTS = 5
MAX_NOTIFICATION_NAMES = 200
NOTIFICATION_PAGE_MAX = 100
DASHBOARD_NOTIFICATIONS = 5

# Grievance attachments. The File hook in hostel360.security.files applies the same policy
# again on insert; checking here first gives the portal a clear message before any write.
UPLOAD_PURPOSES = ("grievance",)
UPLOAD_FOLDER = "Home/Attachments"
UPLOAD_EXTENSIONS = frozenset({"pdf", "jpg", "jpeg", "png", "webp"})
UPLOAD_MAX_BYTES = 5 * 1024 * 1024
# Uploads not attached to a grievance within a day are removed by delete_stale_uploads().
UNATTACHED_UPLOADS_PER_DAY = 20
STALE_UPLOAD_HOURS = 24
STALE_UPLOAD_BATCH = 500
PHOTO_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp", "gif"})

MINUTE = 60
HOUR = 60 * MINUTE

# (requests, window in seconds) per signed-in user. Generous enough for someone retrying on a
# weak mobile connection, tight enough to stop a script working through one account.
RATE_LIMITS = {
	"accept_privacy_notice": (20, HOUR),
	"confirm_profile": (20, HOUR),
	"create_payment": (30, HOUR),
	"verify_payment": (60, HOUR),
	"download": (60, 10 * MINUTE),
	"photo": (300, 10 * MINUTE),
	"apply_mess_reduction": (20, HOUR),
	"cancel_mess_reduction": (30, HOUR),
	"apply_leave": (20, HOUR),
	"cancel_leave": (30, HOUR),
	"decide_leave": (60, HOUR),
	"raise_grievance": (10, HOUR),
	"comment_grievance": (60, HOUR),
	"reopen_grievance": (10, HOUR),
	"rate_grievance": (20, HOUR),
	"upload_attachment": (30, HOUR),
	"submit_feedback": (20, HOUR),
	"request_bonafide": (10, HOUR),
	"mark_notifications_read": (120, HOUR),
}

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class PortalValidationPending(frappe.PermissionError):
	"""The student's registration has not been validated by the hostel office yet."""


class PortalConsentRequired(frappe.PermissionError):
	"""The caller has not accepted the current version of the privacy notice."""


# ============================================================================ rate limits


def rate_limited(action: str):
	"""Limit how often the signed-in user may call an endpoint (see ``RATE_LIMITS``)."""
	if action not in RATE_LIMITS:
		raise ValueError(f"No rate limit configured for {action}")

	def decorator(fn):
		@wraps(fn)
		def wrapper(*args, **kwargs):
			check_rate_limit(action)
			return fn(*args, **kwargs)

		return wrapper

	return decorator


def check_rate_limit(action: str, user: str | None = None) -> None:
	# Only HTTP requests are limited, as with Frappe's own decorator; tests opt in with a flag.
	if getattr(frappe.local, "request", None) is None and not frappe.flags.hostel360_enforce_rate_limits:
		return
	user = user or frappe.session.user
	if not user or user == "Guest":
		# Guests are refused by the identity check that follows.
		return

	limit, window = RATE_LIMITS[action]
	key = frappe.cache.make_key(f"hostel360:portal-rate:{action}:{user}")
	count = frappe.cache.incrby(key, 1)
	# Re-arm a missing expiry as well, so a lost EXPIRE can never lock an account for good.
	if count == 1 or frappe.cache.ttl(key) < 0:
		frappe.cache.expire(key, window)
	if count > limit:
		frappe.throw(
			_("You have done this too many times in a short while. Please wait a few minutes and try again."),
			frappe.RateLimitExceededError,
			title=_("Please slow down"),
		)


# ============================================================================ caller resolution


def _identity(kind: str | None = None) -> frappe._dict:
	"""The portal identity of the session user, with their own Student or Guardian record."""
	identity = require_portal_identity()
	if kind and identity.kind != kind:
		if kind == PARENT:
			frappe.throw(_("This is available to parents and guardians only."), frappe.PermissionError)
		frappe.throw(_("This is available to students only."), frappe.PermissionError)

	if not _portal_enabled(PORTAL_SWITCHES[identity.kind]):
		if identity.kind == STUDENT:
			frappe.throw(_("The student portal is not available right now. Please try again later."), frappe.PermissionError)
		frappe.throw(_("The parent portal is not available right now. Please try again later."), frappe.PermissionError)

	identity.user = frappe.session.user
	identity.record = _caller_record(identity)
	if identity.kind == PARENT and identity.record.validation_status == "Rejected":
		frappe.throw(
			_("Portal access for this parent record has been closed. Please contact the hostel office."),
			frappe.PermissionError,
		)
	return identity


def _caller_record(identity) -> frappe._dict:
	"""The caller's own Hostel Student or Hostel Guardian row (fields the portal needs)."""
	if identity.kind == STUDENT:
		row = frappe.db.get_value(
			STUDENT_DOCTYPE,
			identity.student,
			[
				"name",
				"full_name",
				"mobile_number",
				"validation_status",
				"profile_confirmed",
				"privacy_consent_version",
				"privacy_consent_on",
			],
			as_dict=True,
		)
		if row:
			row.update(doctype=STUDENT_DOCTYPE, display_name=row.full_name)
	else:
		row = frappe.db.get_value(
			GUARDIAN_DOCTYPE,
			identity.guardian,
			["name", "guardian_name", "mobile_number", "validation_status", "privacy_consent_version", "privacy_consent_on"],
			as_dict=True,
		)
		if row:
			row.update(doctype=GUARDIAN_DOCTYPE, display_name=row.guardian_name, profile_confirmed=1)
	if not row:
		frappe.throw(_("Your portal account is not linked to a hostel record. Please contact the hostel office."), frappe.PermissionError)
	return row


def _portal(student: str | None = None, *, kind: str | None = None, full_access: bool = True, write: bool = False):
	"""Resolve the caller and the student a call is about.

	A student may only act for themselves; a parent for one of their wards. When a parent
	does not say which ward, a read uses the first one, but a write must name the student so a
	request is never filed for the wrong child.
	"""
	identity = _identity(kind)
	selected = _select_student(identity, student, write)
	if full_access:
		_assert_full_access(identity, selected)
	return frappe._dict(identity=identity, kind=identity.kind, user=identity.user, student=selected)


def _portal_for_record(
	doctype: str,
	name,
	label: str,
	*,
	kind: str | None = None,
	full_access: bool = True,
	student_field: str = "student",
):
	"""Resolve the caller for a call naming one record, and check the record is theirs."""
	identity = _identity(kind)
	name = _clean_name(name, label)
	student = frappe.db.get_value(doctype, name, student_field)
	if not student:
		frappe.throw(_("{0} {1} was not found.").format(label, name), frappe.DoesNotExistError)
	if student not in identity.wards:
		frappe.throw(_("You are not permitted to access {0} {1}.").format(label, name), frappe.PermissionError)
	if full_access:
		_assert_full_access(identity, student)
	return frappe._dict(identity=identity, kind=identity.kind, user=identity.user, student=student, name=name)


def _select_student(identity, student, write: bool) -> str:
	requested = _clean_name(student, _("Student")) if student not in (None, "") else None

	if identity.kind == STUDENT:
		if requested and requested != identity.student:
			frappe.throw(_("You can only see your own records."), frappe.PermissionError)
		return identity.student

	wards = identity.wards or []
	if requested:
		if requested not in wards:
			frappe.throw(_("This student is not linked to your account."), frappe.PermissionError)
		return requested
	if not wards:
		frappe.throw(
			_("No student is linked to your account yet. Please contact the hostel office."),
			frappe.PermissionError,
		)
	if write and len(wards) > 1:
		frappe.throw(_("Please choose the student this request is for."))
	return wards[0]


def _assert_full_access(identity, student: str) -> None:
	"""Full portal features need an accepted privacy notice and a validated student (TR 3.1, 4.1)."""
	if _consent_required(identity.record):
		frappe.throw(
			_("Please read and accept the privacy notice to continue."),
			PortalConsentRequired,
			title=_("Privacy notice"),
		)

	row = frappe.db.get_value(STUDENT_DOCTYPE, student, ["full_name", "validation_status"], as_dict=True)
	status = row.validation_status if row else None
	if status == "Validated":
		return
	if status == "Rejected":
		message = _("This hostel registration was not validated. Please contact the hostel office.")
	elif identity.kind == STUDENT:
		message = _(
			"Your hostel registration is waiting for validation by the hostel office. "
			"This will be available once it is validated."
		)
	else:
		message = _(
			"{0}'s hostel registration is waiting for validation by the hostel office. "
			"This will be available once it is validated."
		).format(row.full_name if row else student)
	frappe.throw(message, PortalValidationPending, title=_("Registration not validated yet"))


def _consent_required(record) -> bool:
	current = _privacy_notice_version()
	if not current:
		return False
	return (record.get("privacy_consent_version") or "").strip() != current


def _privacy_notice_version() -> str:
	# Read the stored value exactly as record_privacy_consent() does, so the two always agree.
	return (frappe.db.get_single_value("Hostel Settings", "privacy_notice_version") or "").strip()


def _portal_enabled(fieldname: str) -> bool:
	"""Hostel Settings portal switch; one that was never saved counts as on (as at sign-in)."""
	try:
		from hostel360.auth.otp import portal_enabled
	except ImportError:
		value = frappe.db.get_singles_dict("Hostel Settings").get(fieldname)
		return value in (None, "") or cint(value) == 1
	return portal_enabled(fieldname)


# ============================================================================ input checks


def _clean_name(value, label: str) -> str:
	if not isinstance(value, str | int):
		frappe.throw(_("{0} is not valid.").format(label))
	value = CONTROL_CHARS.sub("", str(value)).strip()
	if not value:
		frappe.throw(_("{0} is required.").format(label), frappe.MandatoryError)
	if len(value) > NAME_MAX:
		frappe.throw(_("{0} is not valid.").format(label))
	return value


def _text(value, label: str, max_length: int, *, required: bool = False, multiline: bool = True) -> str | None:
	if value is None:
		value = ""
	if not isinstance(value, str | int | float):
		frappe.throw(_("{0} is not valid.").format(label))
	value = CONTROL_CHARS.sub("", str(value))
	if not multiline:
		value = " ".join(value.split())
	value = value.strip()
	if not value:
		if required:
			frappe.throw(_("{0} is required.").format(label), frappe.MandatoryError)
		return None
	if len(value) > max_length:
		frappe.throw(_("{0} must be at most {1} characters long.").format(label, max_length))
	return value


def _option(doctype: str, fieldname: str, value, label: str, *, default: str | None = None) -> str:
	"""A value from the Select options of the DocType field (the design's enumeration)."""
	value = value.strip() if isinstance(value, str) else value
	if value in (None, ""):
		if default is not None:
			return default
		frappe.throw(_("{0} is required.").format(label), frappe.MandatoryError)
	if not isinstance(value, str):
		frappe.throw(_("{0} is not valid.").format(label))
	try:
		options = [o for o in (frappe.get_meta(doctype).get_options(fieldname) or "").split("\n") if o]
	except frappe.DoesNotExistError:
		options = []
	if options and value not in options:
		frappe.throw(_("{0} must be one of: {1}.").format(label, ", ".join(_(o) for o in options)))
	return value


def _date(value, label: str, *, required: bool = True):
	if value in (None, ""):
		if required:
			frappe.throw(_("{0} is required.").format(label), frappe.MandatoryError)
		return None
	try:
		parsed = getdate(value)
	except Exception:
		parsed = None
	if not parsed or not 2000 <= parsed.year <= 2100:
		frappe.clear_last_message()
		frappe.throw(_("{0} is not a valid date.").format(label))
	return parsed


def _datetime(value, label: str):
	if value in (None, ""):
		frappe.throw(_("{0} is required.").format(label), frappe.MandatoryError)
	try:
		parsed = get_datetime(value)
	except Exception:
		parsed = None
	if not parsed or not 2000 <= parsed.year <= 2100:
		frappe.clear_last_message()
		frappe.throw(_("{0} is not a valid date and time.").format(label))
	return parsed


def _flag(value) -> int:
	if isinstance(value, str):
		return 1 if value.strip().lower() in ("1", "true", "yes", "on") else 0
	return 1 if value else 0


def _stars(value, label: str, *, required: bool = False) -> int | None:
	if value in (None, ""):
		if required:
			frappe.throw(_("Please give a rating for {0}.").format(label), frappe.MandatoryError)
		return None
	try:
		stars = int(value)
	except (TypeError, ValueError):
		stars = -1
	if stars < 0 or stars > 5 or (required and stars == 0):
		frappe.throw(_("{0} must be between 1 and 5 stars.").format(label))
	return stars


def _name_list(value, label: str, max_items: int, *, cleaner=None) -> list[str]:
	"""A list of names from a JSON array, a list, or a comma-separated string."""
	cleaner = cleaner or _clean_name
	if value in (None, "", []):
		return []
	if isinstance(value, str):
		text = value.strip()
		if text.startswith("["):
			try:
				value = json.loads(text)
			except ValueError:
				frappe.throw(_("{0} is not valid.").format(label))
		else:
			value = text.split(",")
	if not isinstance(value, list | tuple):
		frappe.throw(_("{0} is not valid.").format(label))
	names = []
	for item in value:
		if item in (None, ""):
			continue
		name = cleaner(item, label)
		if name not in names:
			names.append(name)
	if len(names) > max_items:
		frappe.throw(_("Please choose at most {0} items for {1}.").format(max_items, label))
	return names


def _attachment_url(value, label: str) -> str:
	if not isinstance(value, str):
		frappe.throw(_("{0} is not valid.").format(label))
	url = value.strip()
	if not url.startswith("/private/files/") or len(url) > FILE_URL_MAX or ".." in url:
		frappe.throw(_("Attachment {0} was not found. Please upload it again.").format(url.rsplit("/", 1)[-1][:60]))
	return url


def _parse_dict(value, label: str) -> dict:
	if isinstance(value, str):
		try:
			value = json.loads(value or "{}")
		except ValueError:
			frappe.throw(_("{0} is not valid.").format(label))
	if not isinstance(value, dict):
		frappe.throw(_("{0} is not valid.").format(label))
	return value


# ============================================================================ session and privacy


@frappe.whitelist(methods=["GET", "POST"])
def get_session() -> dict:
	"""Who is signed in, their wards and the portal settings the app needs at start-up."""
	identity = _identity()
	record = identity.record
	notice_version = _privacy_notice_version()

	return {
		"user": identity.user,
		"kind": identity.kind,
		"display_name": record.display_name,
		"mobile_masked": mask_mobile(record.mobile_number),
		"wards": _ward_cards(identity.wards),
		"validation_status": record.validation_status,
		"profile_confirmed": bool(cint(record.profile_confirmed)),
		"consent_required": _consent_required(record),
		"privacy_notice_version": notice_version or None,
		"privacy_consent_version": record.privacy_consent_version,
		"unread_notifications": _unread_count(identity.user),
		"settings": _session_settings(),
	}


def _ward_cards(wards: list[str]) -> list[dict]:
	if not wards:
		return []
	rows = {
		row.name: row
		for row in frappe.get_all(
			STUDENT_DOCTYPE,
			filters={"name": ("in", wards)},
			fields=[
				"name",
				"full_name",
				"register_number",
				"photo",
				"current_hostel",
				"current_room",
				"current_bed",
				"status",
				"validation_status",
			],
		)
	}
	hostels = _names_of("Hostel", {r.current_hostel for r in rows.values()}, "hostel_name")
	rooms = _names_of("Hostel Room", {r.current_room for r in rows.values()}, "room_number")
	beds = _names_of("Hostel Bed", {r.current_bed for r in rows.values()}, "bed_label")

	cards = []
	for name in wards:
		row = rows.get(name)
		if not row:
			continue
		cards.append(
			{
				"student": row.name,
				"full_name": row.full_name,
				"register_number": row.register_number,
				"photo": photo_url(row.name, row.photo),
				"hostel": row.current_hostel,
				"hostel_name": hostels.get(row.current_hostel),
				"room": row.current_room,
				"room_number": rooms.get(row.current_room),
				"bed": row.current_bed,
				"bed_label": beds.get(row.current_bed),
				"status": row.status,
				"validation_status": row.validation_status,
			}
		)
	return cards


def _names_of(doctype: str, names: set, fieldname: str) -> dict:
	names = [n for n in names if n]
	if not names:
		return {}
	return {
		row.name: row.get(fieldname)
		for row in frappe.get_all(doctype, filters={"name": ("in", names)}, fields=["name", fieldname])
	}


def photo_url(student: str, photo: str | None) -> str | None:
	"""A URL the portal can show. Private photos are served through ``download_photo``."""
	if not photo:
		return None
	if photo.startswith("/private/"):
		return f"{PHOTO_ENDPOINT}?student={quote(student, safe='')}"
	return photo


def _session_settings() -> dict:
	settings = get_branding()
	settings.update(
		payment_enabled=_optional("payment settings", _payment_enabled) or False,
		mess_reduction_min_days=_optional("mess settings", _mess_reduction_min_days),
		leave_requires_parent_approval=bool(_optional("leave settings", _leave_requires_parent_approval)),
	)
	return settings


def get_branding() -> dict:
	"""Institution name, logo and HyFib support contacts shown on every portal page."""
	fields = ("institution_name", "institution_short_name", "institution_logo", "support_phone", "support_email")
	values = dict.fromkeys(fields)
	try:
		settings = frappe.get_cached_doc("Hostel Settings")
		meta = frappe.get_meta("Hostel Settings")
	except frappe.DoesNotExistError:
		settings = meta = None

	for fieldname in fields:
		value = settings.get(fieldname) if settings else None
		if value in (None, "") and meta:
			# A value never saved falls back to the default the DocType declares.
			field = meta.get_field(fieldname)
			value = field.default if field else None
		values[fieldname] = value or None

	logo = values.pop("institution_logo")
	values["logo"] = logo if logo and not logo.startswith("/private/") else None
	return values


def _payment_enabled() -> bool:
	from hostel360.hostel_fees.utils import get_payment_settings

	return bool(cint(get_payment_settings().get("enabled")))


def _mess_reduction_min_days() -> int:
	from hostel360.hostel_mess.utils import get_mess_settings

	return cint(get_mess_settings().reduction_min_days)


def _leave_requires_parent_approval() -> bool:
	from hostel360.student_services.utils import get_setting

	return bool(cint(get_setting("leave_requires_parent_approval")))


@frappe.whitelist(methods=["GET", "POST"])
def get_privacy_notice() -> dict:
	"""The current privacy notice and what the caller has accepted."""
	from frappe.utils.html_utils import sanitize_html

	identity = _identity()
	record = identity.record
	notice = frappe.db.get_single_value("Hostel Settings", "privacy_notice") or ""
	return {
		"version": _privacy_notice_version() or None,
		"notice": sanitize_html(notice),
		"accepted_version": record.privacy_consent_version,
		"accepted_on": record.privacy_consent_on,
		"consent_required": _consent_required(record),
	}


@frappe.whitelist(methods=["POST"])
@rate_limited("accept_privacy_notice")
def accept_privacy_notice(version: str) -> dict:
	"""Record that the caller accepted this version of the privacy notice (DPDP Act consent)."""
	from hostel360.hostel_people.services import record_privacy_consent

	identity = _identity()
	version = _text(version, _("Version"), 20, required=True, multiline=False)
	record_privacy_consent(identity.record.doctype, identity.record.name, version)
	return {"ok": True}


# ============================================================================ profile


@frappe.whitelist(methods=["GET", "POST"])
def get_profile(student: str | None = None) -> dict:
	"""Profile of the student (or the selected ward), with guardians' mobiles masked."""
	from hostel360.hostel_people.services import get_student_summary

	ctx = _portal(student, full_access=False)
	profile = get_student_summary(ctx.student)
	profile["photo"] = photo_url(ctx.student, profile.get("photo"))
	profile["can_edit"] = ctx.kind == STUDENT
	return profile


@frappe.whitelist(methods=["POST"])
@rate_limited("confirm_profile")
def confirm_profile(updates: dict | str | None = None) -> dict:
	"""The student confirms their details, correcting contact fields where needed."""
	from hostel360.hostel_people.services import PROFILE_EDITABLE_FIELDS
	from hostel360.hostel_people.services import confirm_profile as confirm_student_profile

	ctx = _portal(kind=STUDENT, full_access=False)
	updates = _parse_dict(updates or {}, _("Profile details"))
	if len(updates) > len(PROFILE_EDITABLE_FIELDS) * 2:
		frappe.throw(_("Profile details are not valid."))
	confirm_student_profile(ctx.student, {key: updates[key] for key in PROFILE_EDITABLE_FIELDS if key in updates})
	return {"ok": True}


@frappe.whitelist(methods=["GET"])
@rate_limited("photo")
def download_photo(student: str | None = None) -> None:
	"""The student's photo, for the portal header and profile (photos are private files)."""
	ctx = _portal(student, full_access=False)
	photo = frappe.db.get_value(STUDENT_DOCTYPE, ctx.student, "photo")
	file_name = (
		frappe.db.get_value("File", {"file_url": photo, "is_folder": 0}, "name") if photo else None
	)
	extension = (photo or "").rsplit(".", 1)[-1].lower()
	if not file_name or extension not in PHOTO_EXTENSIONS:
		frappe.throw(_("No photo is on record."), frappe.DoesNotExistError)

	# Read the bytes directly: File.get_content() may try to decode them as text.
	with open(frappe.get_doc("File", file_name).get_full_path(), "rb") as fh:
		content = fh.read()
	frappe.local.response.filename = f"photo.{extension}"
	frappe.local.response.filecontent = content
	frappe.local.response.content_type = guess_type(f"photo.{extension}")[0] or "application/octet-stream"
	frappe.local.response.display_content_as = "inline"
	frappe.local.response.type = "download"
	headers = getattr(frappe.local, "response_headers", None)
	if headers is not None:
		headers.set("Cache-Control", "private, max-age=3600")


# ============================================================================ dashboard


@frappe.whitelist(methods=["GET", "POST"])
def get_dashboard(student: str | None = None) -> dict:
	"""Home screen: room, dues, today's menu, open requests and the latest notifications."""
	from hostel360.hostel_people.services import get_student_summary

	ctx = _portal(student)
	summary = _optional("profile", get_student_summary, ctx.student) or {}
	fees = _optional("fees", _fee_totals, ctx.student)
	return {
		"student": ctx.student,
		"student_name": summary.get("full_name"),
		"room": summary.get("room"),
		"fees": fees,
		"today_menu": _optional("menu", _today_menu, ctx.student),
		"pending": _optional("pending", _pending_counts, ctx) or {},
		"notifications": _notification_rows(ctx.user, DASHBOARD_NOTIFICATIONS),
		"unread_notifications": _unread_count(ctx.user),
	}


def _optional(section: str, fn, *args):
	"""Run one part of a composite response; if it fails, log it and leave that part empty.

	The home screen should still open when, say, the mess module has no menu configured.
	Permission problems are never swallowed.
	"""
	message_log = frappe.local.message_log
	messages = len(message_log or [])
	try:
		return fn(*args)
	except frappe.PermissionError:
		raise
	except Exception:
		if message_log is not None:
			# Drop the error popup the failed part queued; the rest of the response is fine.
			del message_log[messages:]
		frappe.log_error(title=f"Hostel360 portal: {section} unavailable", message=frappe.get_traceback())
		return None


def _fee_totals(student: str) -> dict:
	from hostel360.hostel_fees.services import get_fee_summary

	summary = get_fee_summary(student)
	return {
		"outstanding": summary.get("outstanding"),
		"overdue": summary.get("overdue"),
		"overdue_count": summary.get("overdue_count"),
		"next_due_date": summary.get("next_due_date"),
	}


def _today_menu(student: str):
	from hostel360.hostel_mess.services import get_today_menu

	return get_today_menu(student)


def _pending_counts(ctx) -> dict:
	try:
		from hostel360.student_services.doctype.home_visit_leave.home_visit_leave import PENDING_STATUSES
	except ImportError:
		PENDING_STATUSES = LEAVE_PENDING_STATUSES
	try:
		from hostel360.student_services.doctype.hostel_grievance.hostel_grievance import OPEN_STATUSES
	except ImportError:
		OPEN_STATUSES = GRIEVANCE_OPEN_STATUSES

	student = ctx.student
	open_grievances = frappe.get_all(
		GRIEVANCE,
		filters={"student": student, "status": ("in", OPEN_STATUSES)},
		fields=["name", "is_confidential", "raised_by_user"],
	)
	counts = {
		"leaves": frappe.db.count(LEAVE, {"student": student, "status": ("in", PENDING_STATUSES)}),
		"reductions": frappe.db.count(REDUCTION, {"student": student, "status": "Pending"}),
		"grievances": len([row for row in open_grievances if _grievance_visible(ctx, row)]),
		"certificates": frappe.db.count(BONAFIDE, {"student": student, "status": "Pending"}),
	}
	if ctx.kind == PARENT:
		counts["approvals"] = len(_pending_approvals_for(ctx.identity))
	return counts


# ============================================================================ fees and payments


@frappe.whitelist(methods=["GET", "POST"])
def get_fees(student: str | None = None) -> dict:
	"""Invoices with their items, totals due and the payments made."""
	from hostel360.hostel_fees.services import get_fee_summary

	ctx = _portal(student)
	return get_fee_summary(ctx.student)


@frappe.whitelist(methods=["POST"])
@rate_limited("create_payment")
def create_payment(invoices: list[str] | str, student: str | None = None) -> dict:
	"""Start an online payment for some of the student's invoices; returns the checkout options."""
	from hostel360.hostel_fees.services import create_payment_order

	ctx = _portal(student, write=True)
	names = _name_list(invoices, _("Invoices"), MAX_INVOICES)
	if not names:
		frappe.throw(_("Select at least one invoice to pay."))
	return create_payment_order(ctx.student, names, ctx.user, getattr(frappe.local, "request_ip", None))


@frappe.whitelist(methods=["POST"])
@rate_limited("verify_payment")
def verify_payment(
	transaction: str,
	razorpay_payment_id: str,
	razorpay_order_id: str,
	razorpay_signature: str,
) -> dict:
	"""Confirm a payment from the checkout callback. The gateway is asked before any receipt is issued."""
	from hostel360.hostel_fees.services import confirm_checkout

	# The payment was started after passing every portal check; confirming money already paid
	# must not stop because, say, a new privacy notice was published in between.
	ctx = _portal_for_record(TRANSACTION, transaction, _("Payment"), full_access=False)
	payload = {
		"razorpay_payment_id": _text(razorpay_payment_id, _("Payment ID"), 64, required=True, multiline=False),
		"razorpay_order_id": _text(razorpay_order_id, _("Order ID"), 64, required=True, multiline=False),
		"razorpay_signature": _text(razorpay_signature, _("Signature"), 256, required=True, multiline=False),
	}
	return confirm_checkout(ctx.name, payload)


@frappe.whitelist(methods=["GET", "POST"])
@rate_limited("download")
@frappe.concurrent_limit()
def download_receipt(payment: str) -> None:
	"""Fee receipt as PDF."""
	from hostel360.hostel_fees.services import get_receipt_pdf

	ctx = _portal_for_record(PAYMENT, payment, _("Receipt"))
	_send_pdf(get_receipt_pdf(ctx.name), f"Receipt-{ctx.name}")


@frappe.whitelist(methods=["GET", "POST"])
@rate_limited("download")
@frappe.concurrent_limit()
def download_fee_structure(student: str | None = None) -> None:
	"""The fee structure that applies to the student, as PDF."""
	from hostel360.hostel_fees.services import get_fee_structure_pdf

	ctx = _portal(student)
	register_number = frappe.db.get_value(STUDENT_DOCTYPE, ctx.student, "register_number") or ctx.student
	_send_pdf(get_fee_structure_pdf(ctx.student), f"Fee-Structure-{register_number}")


def _send_pdf(content: bytes, file_stem: str) -> None:
	stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", file_stem).strip("-") or "document"
	frappe.local.response.filename = f"{stem}.pdf"
	frappe.local.response.filecontent = content
	frappe.local.response.type = "pdf"


# ============================================================================ mess


@frappe.whitelist(methods=["GET", "POST"])
def get_menu(week_start: str | None = None, student: str | None = None) -> dict:
	"""Published menu of the student's mess for the week containing ``week_start`` (default: this week)."""
	from hostel360.hostel_mess.services import get_mess_for_student, get_week_menu

	ctx = _portal(student)
	start = _date(week_start, _("Week"), required=False)
	mess = get_mess_for_student(ctx.student)
	return get_week_menu(mess, str(start) if start else None)


@frappe.whitelist(methods=["GET", "POST"])
def get_mess_bills(student: str | None = None) -> list[dict]:
	"""Published monthly mess bills with the status of their invoices."""
	from hostel360.hostel_mess.services import list_mess_bills

	ctx = _portal(student)
	return list_mess_bills(ctx.student)


@frappe.whitelist(methods=["GET", "POST"])
def list_mess_reductions(student: str | None = None) -> list[dict]:
	from hostel360.hostel_mess.services import list_mess_reductions as list_reductions

	ctx = _portal(student)
	return list_reductions(ctx.student)


@frappe.whitelist(methods=["POST"])
@rate_limited("apply_mess_reduction")
def apply_mess_reduction(
	from_date: str,
	to_date: str,
	reason: str,
	description: str | None = None,
	student: str | None = None,
) -> dict:
	"""Apply for a mess bill reduction for days away from the hostel."""
	from hostel360.hostel_mess.services import apply_mess_reduction as apply_reduction

	ctx = _portal(student, write=True)
	start = _date(from_date, _("From Date"))
	end = _date(to_date, _("To Date"))
	reason = _option(REDUCTION, "reason", reason, _("Reason"))
	description = _text(description, _("Description"), REASON_MAX)
	name = apply_reduction(ctx.student, start, end, reason, description=description, user=ctx.user)
	return {"name": name}


@frappe.whitelist(methods=["POST"])
@rate_limited("cancel_mess_reduction")
def cancel_mess_reduction(name: str) -> dict:
	from hostel360.hostel_mess.services import cancel_mess_reduction as cancel_reduction

	ctx = _portal_for_record(REDUCTION, name, _("Mess reduction"))
	cancel_reduction(ctx.name, ctx.user)
	return {"ok": True}


# ============================================================================ leave


@frappe.whitelist(methods=["GET", "POST"])
def list_leaves(student: str | None = None) -> list[dict]:
	from hostel360.student_services.services import list_leaves as student_leaves

	ctx = _portal(student)
	return student_leaves(ctx.student)


@frappe.whitelist(methods=["POST"])
@rate_limited("apply_leave")
def apply_leave(
	from_datetime: str,
	to_datetime: str,
	destination: str,
	reason: str,
	contact_during_leave: str | None = None,
	apply_mess_reduction: bool = False,
	student: str | None = None,
) -> dict:
	"""Apply for home-visit leave. A parent applying for their ward skips the parent approval step."""
	from hostel360.student_services.services import apply_leave as create_leave

	ctx = _portal(student, write=True)
	data = {
		"from_datetime": _datetime(from_datetime, _("From")),
		"to_datetime": _datetime(to_datetime, _("To")),
		"destination": _text(destination, _("Destination"), DESTINATION_MAX, required=True, multiline=False),
		"reason": _text(reason, _("Reason"), REASON_MAX, required=True),
		"contact_during_leave": _text(contact_during_leave, _("Contact during leave"), CONTACT_MAX, multiline=False),
		"apply_mess_reduction": _flag(apply_mess_reduction),
	}
	applied_by = "Student" if ctx.kind == STUDENT else "Parent"
	name = create_leave(ctx.student, data, applied_by, ctx.user)
	return {"name": name, "status": frappe.db.get_value(LEAVE, name, "status")}


@frappe.whitelist(methods=["POST"])
@rate_limited("cancel_leave")
def cancel_leave(name: str) -> dict:
	from hostel360.student_services.services import cancel_leave as withdraw_leave

	ctx = _portal_for_record(LEAVE, name, _("Leave"))
	withdraw_leave(ctx.name, ctx.user)
	return {"ok": True, "status": frappe.db.get_value(LEAVE, ctx.name, "status")}


@frappe.whitelist(methods=["GET", "POST"])
def pending_leave_approvals() -> list[dict]:
	"""Leave requests of the parent's wards that are waiting for this parent's decision."""
	identity = _identity(PARENT)
	if _consent_required(identity.record):
		frappe.throw(
			_("Please read and accept the privacy notice to continue."),
			PortalConsentRequired,
			title=_("Privacy notice"),
		)
	return _pending_approvals_for(identity)


def _pending_approvals_for(identity) -> list[dict]:
	from hostel360.student_services.services import list_pending_parent_approvals

	validated = set(
		frappe.get_all(
			STUDENT_DOCTYPE,
			filters={"name": ("in", identity.wards or [""]), "validation_status": "Validated"},
			pluck="name",
		)
	)
	if not validated:
		return []
	return [row for row in list_pending_parent_approvals(identity.guardian) if row.get("student") in validated]


@frappe.whitelist(methods=["POST"])
@rate_limited("decide_leave")
def decide_leave(name: str, action: str, remarks: str | None = None) -> dict:
	"""A parent approves or rejects their ward's leave request."""
	from hostel360.student_services.services import parent_decision

	ctx = _portal_for_record(LEAVE, name, _("Leave"), kind=PARENT)
	action = (action or "").strip().lower() if isinstance(action, str) else ""
	if action not in ("approve", "reject"):
		frappe.throw(_("Please choose to approve or reject the leave."))
	remarks = _text(remarks, _("Remarks"), REMARKS_MAX)
	parent_decision(ctx.name, ctx.identity.guardian, action == "approve", remarks)
	return {"ok": True, "status": frappe.db.get_value(LEAVE, ctx.name, "status")}


# ============================================================================ grievances


def _grievance_visible(ctx, row) -> bool:
	"""A confidential grievance is shown to the student, and to a parent only if they raised it.

	Ragging and harassment complaints are always confidential; a resident must be able to
	raise one without it appearing in a parent's portal.
	"""
	if ctx.kind == STUDENT:
		return True
	return not cint(row.get("is_confidential")) or row.get("raised_by_user") == ctx.user


def _grievance_for(name, *, rating: bool = False):
	ctx = _portal_for_record(GRIEVANCE, name, _("Grievance"))
	row = frappe.db.get_value(GRIEVANCE, ctx.name, ["is_confidential", "raised_by_user"], as_dict=True)
	if not _grievance_visible(ctx, row):
		frappe.throw(_("You are not permitted to access Grievance {0}.").format(ctx.name), frappe.PermissionError)
	if rating and ctx.kind == PARENT and row.raised_by_user != ctx.user:
		frappe.throw(_("Only the student or the person who raised this grievance can rate it."), frappe.PermissionError)
	return ctx


@frappe.whitelist(methods=["GET", "POST"])
def list_grievances(student: str | None = None) -> list[dict]:
	from hostel360.student_services.services import list_grievances as student_grievances

	ctx = _portal(student)
	rows = student_grievances(ctx.student)
	if ctx.kind == PARENT:
		hidden = {
			row.name
			for row in frappe.get_all(
				GRIEVANCE,
				filters={"student": ctx.student, "is_confidential": 1},
				fields=["name", "is_confidential", "raised_by_user"],
			)
			if not _grievance_visible(ctx, row)
		}
		rows = [row for row in rows if row.get("name") not in hidden]
	return rows


@frappe.whitelist(methods=["GET", "POST"])
def get_grievance(name: str) -> dict:
	"""A grievance with its public timeline (internal staff notes are never included)."""
	from hostel360.student_services.services import get_grievance as grievance_detail

	ctx = _grievance_for(name)
	detail = grievance_detail(ctx.name)
	if ctx.kind == PARENT:
		raised_by = frappe.db.get_value(GRIEVANCE, ctx.name, "raised_by_user")
		detail["can_rate"] = bool(detail.get("can_rate")) and raised_by == ctx.user
	return detail


@frappe.whitelist(methods=["POST"])
@rate_limited("raise_grievance")
def raise_grievance(
	category: str,
	subject: str,
	description: str,
	priority: str | None = None,
	is_confidential: bool = False,
	attachments: list[str] | str | None = None,
	student: str | None = None,
) -> dict:
	"""Raise a grievance. Attachments are file URLs returned by ``upload_attachment``."""
	from hostel360.student_services.services import raise_grievance as create_grievance

	ctx = _portal(student, write=True)
	data = {
		"category": _option(GRIEVANCE, "category", category, _("Category")),
		"subject": _text(subject, _("Subject"), SUBJECT_MAX, required=True, multiline=False),
		"description": _text(description, _("Description"), DESCRIPTION_MAX, required=True),
		"priority": _option(GRIEVANCE, "priority", priority, _("Priority"), default="Medium"),
		"is_confidential": _flag(is_confidential),
	}
	files = _name_list(attachments, _("Attachments"), MAX_ATTACHMENTS, cleaner=_attachment_url)
	name = create_grievance(ctx.student, data, ctx.user, attachments=files)
	return {"name": name}


@frappe.whitelist(methods=["POST"])
@rate_limited("comment_grievance")
def comment_grievance(name: str, comment: str) -> dict:
	from hostel360.student_services.services import add_grievance_comment

	ctx = _grievance_for(name)
	add_grievance_comment(ctx.name, _text(comment, _("Comment"), COMMENT_MAX, required=True), ctx.user)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
@rate_limited("reopen_grievance")
def reopen_grievance(name: str, reason: str) -> dict:
	from hostel360.student_services.services import reopen_grievance as reopen

	ctx = _grievance_for(name)
	reopen(ctx.name, _text(reason, _("Reason"), COMMENT_MAX, required=True), ctx.user)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
@rate_limited("rate_grievance")
def rate_grievance(name: str, rating: int, feedback: str | None = None) -> dict:
	from hostel360.student_services.services import rate_grievance as rate

	ctx = _grievance_for(name, rating=True)
	stars = _stars(rating, _("Rating"), required=True)
	rate(ctx.name, stars, _text(feedback, _("Feedback"), COMMENT_MAX), ctx.user)
	return {"ok": True}


# ============================================================================ attachments


@frappe.whitelist(methods=["POST"])
@rate_limited("upload_attachment")
def upload_attachment(purpose: str = "grievance", student: str | None = None) -> dict:
	"""Upload one file (multipart field ``file``) to attach to a request raised next.

	The file is stored privately, owned by the caller and attached to nothing until
	``raise_grievance`` claims it; only the uploader can claim it.
	"""
	ctx = _portal(student)
	purpose = (purpose or "").strip().lower() if isinstance(purpose, str) else ""
	if purpose not in UPLOAD_PURPOSES:
		frappe.throw(_("Files can only be uploaded for a grievance."))

	upload = _request_file()
	file_name, content = _checked_upload(upload)
	_check_upload_quota(ctx.user)

	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"decode": False,
			"is_private": 1,
			"folder": UPLOAD_FOLDER,
		}
	)
	doc.insert(ignore_permissions=True)
	return {"file_url": doc.file_url, "file_name": doc.file_name, "file_size": doc.file_size}


def _request_file():
	request = getattr(frappe.local, "request", None)
	files = getattr(request, "files", None)
	upload = files.get("file") if files else None
	if not upload or not getattr(upload, "filename", None):
		frappe.throw(_("Please choose a file to upload."))
	return upload


def _checked_upload(upload) -> tuple[str, bytes]:
	"""Clean file name and content after the type, size and content checks."""
	# Never read more than one byte past the limit into memory.
	content = upload.stream.read(UPLOAD_MAX_BYTES + 1)
	if isinstance(content, str):
		content = content.encode()

	try:
		from hostel360.security import files as file_policy
	except ImportError:
		file_policy = None

	if file_policy:
		file_name = file_policy.sanitize_file_name(upload.filename)
		extension = file_policy.file_extension(file_name)
	else:
		file_name = _basic_file_name(upload.filename)
		extension = file_name.rpartition(".")[2].lower() if "." in file_name else ""

	if extension not in UPLOAD_EXTENSIONS:
		frappe.throw(
			_("Files of this type cannot be uploaded. Please upload a PDF, JPG, PNG or WEBP file."),
			title=_("Upload Not Allowed"),
		)
	if not content:
		frappe.throw(_("The file is empty. Please choose the file again."), title=_("Upload Not Allowed"))
	if len(content) > UPLOAD_MAX_BYTES:
		frappe.throw(
			_("This file is too large. Please upload a file smaller than {0} MB.").format(UPLOAD_MAX_BYTES // (1024 * 1024)),
			title=_("Upload Not Allowed"),
		)

	matches = file_policy.content_matches(extension, content) if file_policy else _basic_content_matches(extension, content)
	if not matches:
		frappe.throw(
			_(
				"This file does not look like a valid {0} file. It may be damaged or renamed. "
				"Please upload the original file."
			).format(extension.upper()),
			title=_("Upload Not Allowed"),
		)
	return file_name, content


def _basic_file_name(name: str | None) -> str:
	name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
	stem, dot, extension = name.rpartition(".")
	if not dot:
		stem, extension = name, ""
	stem = re.sub(r"[^\w\- ]+", "_", CONTROL_CHARS.sub("", stem)).strip(" _-")[:100] or "file"
	extension = re.sub(r"[^A-Za-z0-9]", "", extension).lower()[:10]
	return f"{stem}.{extension}" if extension else stem


def _basic_content_matches(extension: str, content: bytes) -> bool:
	if extension == "pdf":
		return b"%PDF-" in content[:1024]
	is_png = content.startswith(b"\x89PNG\r\n\x1a\n")
	is_jpeg = content.startswith(b"\xff\xd8\xff")
	is_webp = content[:4] == b"RIFF" and content[8:12] == b"WEBP"
	return is_png or is_jpeg or is_webp


def _check_upload_quota(user: str) -> None:
	since = add_to_date(now_datetime(), hours=-STALE_UPLOAD_HOURS)
	pending = frappe.db.count(
		"File",
		{
			"owner": user,
			"is_folder": 0,
			"attached_to_name": ("is", "not set"),
			"creation": (">", since),
		},
	)
	if pending >= UNATTACHED_UPLOADS_PER_DAY:
		frappe.throw(
			_("You have uploaded many files that are not attached to a request yet. Please submit your request first."),
			frappe.RateLimitExceededError,
		)


def delete_stale_uploads() -> int:
	"""Daily job: remove portal uploads never attached to a request within a day."""
	file_table = frappe.qb.DocType("File")
	has_role = frappe.qb.DocType("Has Role")
	portal_users = (
		frappe.qb.from_(has_role)
		.select(has_role.parent)
		.where(has_role.parenttype == "User")
		.where(has_role.role.isin(list(PORTAL_ROLES)))
	)
	cutoff = add_to_date(now_datetime(), hours=-STALE_UPLOAD_HOURS)
	names = (
		frappe.qb.from_(file_table)
		.select(file_table.name)
		.where(file_table.is_folder == 0)
		.where(file_table.is_private == 1)
		.where(file_table.folder == UPLOAD_FOLDER)
		.where(file_table.attached_to_name.isnull() | (file_table.attached_to_name == ""))
		.where(file_table.creation < cutoff)
		.where(file_table.owner.isin(portal_users))
		.limit(STALE_UPLOAD_BATCH)
	).run(pluck=True)

	removed = 0
	for name in names:
		try:
			frappe.delete_doc("File", name, ignore_permissions=True)
			removed += 1
		except Exception:
			frappe.log_error(title="Hostel360 portal: could not remove an unused upload", message=frappe.get_traceback())
	return removed


# ============================================================================ feedback


@frappe.whitelist(methods=["POST"])
@rate_limited("submit_feedback")
def submit_feedback(
	feedback_type: str,
	overall: int,
	food_quality: int | None = None,
	cleanliness: int | None = None,
	staff_behaviour: int | None = None,
	facilities: int | None = None,
	comments: str | None = None,
	is_anonymous: bool = False,
	period: str | None = None,
) -> dict:
	"""Monthly feedback from a resident; ratings are 1-5 stars (0 or empty when not rated)."""
	from hostel360.student_services.services import submit_feedback as save_feedback

	ctx = _portal(kind=STUDENT, write=True)
	if period not in (None, ""):
		period = period.strip() if isinstance(period, str) else ""
		if not PERIOD_RE.match(period):
			frappe.throw(_("Period must be a month written as YYYY-MM."))
	data = {
		"feedback_type": _option(FEEDBACK, "feedback_type", feedback_type, _("Feedback type")),
		"period": period or None,
		"overall": _stars(overall, _("Overall"), required=True),
		"food_quality": _stars(food_quality, _("Food Quality")),
		"cleanliness": _stars(cleanliness, _("Cleanliness")),
		"staff_behaviour": _stars(staff_behaviour, _("Staff Behaviour")),
		"facilities": _stars(facilities, _("Facilities")),
		"comments": _text(comments, _("Comments"), FEEDBACK_COMMENTS_MAX),
		"is_anonymous": _flag(is_anonymous),
	}
	return {"name": save_feedback(ctx.student, data)}


# ============================================================================ certificates


@frappe.whitelist(methods=["GET", "POST"])
def list_certificates(student: str | None = None) -> list[dict]:
	from hostel360.student_services.services import list_certificates as student_certificates

	ctx = _portal(student)
	return student_certificates(ctx.student)


@frappe.whitelist(methods=["POST"])
@rate_limited("request_bonafide")
def request_bonafide(purpose: str, purpose_details: str | None = None, student: str | None = None) -> dict:
	from hostel360.student_services.services import request_bonafide as create_request

	ctx = _portal(student, write=True)
	purpose = _option(BONAFIDE, "purpose", purpose, _("Purpose"))
	details = _text(purpose_details, _("Purpose details"), REMARKS_MAX)
	return {"name": create_request(ctx.student, purpose, details)}


@frappe.whitelist(methods=["GET", "POST"])
@rate_limited("download")
@frappe.concurrent_limit()
def download_bonafide(name: str) -> None:
	"""An approved bonafide certificate as PDF."""
	from hostel360.student_services.services import get_bonafide_pdf

	ctx = _portal_for_record(BONAFIDE, name, _("Certificate request"))
	number = frappe.db.get_value(BONAFIDE, ctx.name, "certificate_number") or ctx.name
	_send_pdf(get_bonafide_pdf(ctx.name), f"Bonafide-{number}")


# ============================================================================ notifications


@frappe.whitelist(methods=["GET", "POST"])
def list_notifications(limit: int = 20, start: int = 0) -> list[dict]:
	"""The caller's in-app notifications, newest first."""
	identity = _identity()
	limit = min(max(cint(limit), 1), NOTIFICATION_PAGE_MAX)
	return _notification_rows(identity.user, limit, max(cint(start), 0))


@frappe.whitelist(methods=["POST"])
@rate_limited("mark_notifications_read")
def mark_notifications_read(names: list[str] | str | None = None) -> dict:
	"""Mark the given notifications (or all of them) as read. Only the caller's own are touched."""
	identity = _identity()
	selected = _name_list(names, _("Notifications"), MAX_NOTIFICATION_NAMES)

	log = frappe.qb.DocType(NOTIFICATION_LOG)
	query = frappe.qb.update(log).set(log.read, 1).where(log.for_user == identity.user).where(log.read == 0)
	if selected:
		query = query.where(log.name.isin(selected))
	query.run()
	return {"ok": True}


def _notification_rows(user: str, limit: int, start: int = 0) -> list[dict]:
	rows = frappe.get_all(
		NOTIFICATION_LOG,
		filters={"for_user": user},
		fields=["name", "subject", "title", "description", "creation", "read", "link"],
		order_by="creation desc",
		start=start,
		limit=limit,
	)
	return [
		{
			"name": row.name,
			"subject": _plain(row.subject or row.title, 300),
			"message": _plain(row.description, 500),
			"created": row.creation,
			"read": bool(cint(row.read)),
			"link": _portal_link(row.link),
		}
		for row in rows
	]


def _unread_count(user: str) -> int:
	return frappe.db.count(NOTIFICATION_LOG, {"for_user": user, "read": 0})


def _plain(value, limit: int) -> str:
	text = " ".join(strip_html(value or "").split())
	return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _portal_link(link: str | None) -> str | None:
	"""Only portal routes are passed on; a desk link means nothing to a student or parent."""
	link = (link or "").strip()
	if link == PORTAL_HOME or link.startswith(PORTAL_HOME + "/"):
		return link
	return None
