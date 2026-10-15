"""Helpers shared by the Hostel Mess DocTypes, services, reports and tasks."""

import datetime
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.utils import add_days, flt, format_date, getdate

from hostel360.setup.roles import (
	ADMIN_ROLES,
	DEPUTY_WARDEN_ROLE,
	MESS_MANAGER_ROLE,
	WARDEN_ROLE,
)

SETTINGS_DOCTYPE = "Mess Settings"

# Values used until an administrator saves Mess Settings (and written by install_defaults).
DEFAULT_SETTINGS = {
	"reduction_min_days": 3,
	"reduction_advance_notice_days": 1,
	"reduction_approver": "Deputy Warden",
	"max_reduction_days_per_month": 0,
	"bill_due_days": 10,
	"bill_rounding": "Nearest Rupee",
	"low_stock_alert": 1,
}

APPROVER_ROLE_BY_SETTING = {
	"Deputy Warden": DEPUTY_WARDEN_ROLE,
	"Mess Manager": MESS_MANAGER_ROLE,
}

MEAL_ORDER = ("Breakfast", "Lunch", "Snacks", "Dinner")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def in_migration() -> bool:
	"""Legacy data import: accept historical records as supplied (design §5)."""
	return bool(frappe.flags.hostel360_migration)


def get_mess_settings() -> frappe._dict:
	"""Mess Settings with defaults for any value that was never saved.

	``frappe.db.get_single_value`` turns an unsaved Int into 0, which is a legitimate
	setting for several fields here, so read the stored rows and fill the gaps instead.
	"""
	stored = frappe.db.get_singles_dict(SETTINGS_DOCTYPE, cast=True)
	settings = frappe._dict(DEFAULT_SETTINGS)
	for fieldname in DEFAULT_SETTINGS:
		if stored.get(fieldname) is not None:
			settings[fieldname] = stored.get(fieldname)
	return settings


def get_reduction_approver_role(settings: dict | None = None) -> str:
	settings = settings or get_mess_settings()
	return APPROVER_ROLE_BY_SETTING.get(settings.reduction_approver) or DEPUTY_WARDEN_ROLE


def get_reduction_decider_roles(settings: dict | None = None) -> set[str]:
	"""Roles allowed to approve or reject mess reductions."""
	return set(ADMIN_ROLES) | {WARDEN_ROLE, get_reduction_approver_role(settings)}


def round_half_up(value, precision: int = 2) -> float:
	"""Commercial rounding. Bills are audited by hand, so 0.5 always rounds up."""
	quantum = Decimal(1).scaleb(-precision)
	return float(Decimal(str(flt(value))).quantize(quantum, rounding=ROUND_HALF_UP))


def get_week_start(date=None) -> datetime.date:
	date = getdate(date)
	return date - datetime.timedelta(days=date.weekday())


def get_mess_hostels(mess: str) -> list[str]:
	return frappe.get_all(
		"Hostel Mess Hostel",
		filters={"parent": mess, "parenttype": "Hostel Mess", "parentfield": "hostels"},
		pluck="hostel",
		order_by="idx asc",
	)


def get_mess_for_hostel(hostel: str | None) -> str | None:
	"""The active mess serving a hostel."""
	if not hostel:
		return None
	rows = frappe.db.sql(
		"""
		select m.name
		from `tabHostel Mess` m
		inner join `tabHostel Mess Hostel` h
			on h.parent = m.name and h.parenttype = 'Hostel Mess' and h.parentfield = 'hostels'
		where h.hostel = %(hostel)s and m.is_active = 1
		order by m.creation asc
		limit 1
		""",
		{"hostel": hostel},
	)
	return rows[0][0] if rows else None


def get_published_upto(mess: str) -> datetime.date | None:
	"""Last day covered by a published (submitted) mess bill run of the mess.

	Everything that feeds the dividing system on or before this date is closed: stock
	movements, expenses, reductions and extra charges can no longer be added or undone,
	otherwise published bills would silently stop matching the books.
	"""
	if not mess:
		return None
	value = frappe.db.sql(
		"""select max(to_date) from `tabMess Bill Run` where mess = %(mess)s and docstatus = 1""",
		{"mess": mess},
	)[0][0]
	return getdate(value) if value else None


def assert_period_open(mess: str, date, what: str) -> None:
	"""Refuse changes dated inside a published billing period."""
	if in_migration() or not (mess and date):
		return
	upto = get_published_upto(mess)
	if upto and getdate(date) <= upto:
		frappe.throw(
			_(
				"Mess bills of {0} are published up to {1}. {2} dated on or before that day cannot be added or changed."
			).format(frappe.bold(mess), frappe.bold(format_date(upto)), what),
			title=_("Billing Period Closed"),
		)


def date_range_days(from_date, to_date) -> set[int]:
	"""Ordinals of every day in an inclusive range (empty when the range is inverted)."""
	start, end = getdate(from_date), getdate(to_date)
	if start > end:
		return set()
	return set(range(start.toordinal(), end.toordinal() + 1))


def iter_months(from_date, to_date):
	"""(first_day, last_day) of each calendar month touched by a date range."""
	start, end = getdate(from_date), getdate(to_date)
	cursor = start.replace(day=1)
	while cursor <= end:
		next_month = (cursor.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
		yield cursor, add_days(next_month, -1)
		cursor = next_month


# ------------------------------------------------------------------ notifications


def _dispatch(attr: str):
	"""Communication helpers, resolved lazily so this module never fails to import."""
	try:
		from hostel360.hostel_communication import dispatch
	except ImportError:
		return None
	return getattr(dispatch, attr, None)


def send_notification(
	event: str,
	recipients: list[dict] | None,
	context: dict,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	channels: list[str] | None = None,
) -> None:
	"""Queue a notification without letting a messaging problem fail the business action."""
	if in_migration() or not recipients:
		return
	notify = _dispatch("notify")
	if not notify:
		frappe.logger("hostel360").warning(f"Mess notification '{event}' skipped: dispatcher unavailable")
		return
	try:
		notify(
			event,
			recipients,
			context,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			channels=channels,
		)
	except Exception:
		frappe.log_error(
			title=f"Mess notification failed: {event}",
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)


def student_recipients(
	student: str, include_student: bool = True, include_guardians: bool = False
) -> list[dict]:
	fn = _dispatch("recipients_for_student")
	if not fn:
		return []
	return fn(student, include_student=include_student, include_guardians=include_guardians) or []


def user_recipients(users: list[str]) -> list[dict]:
	users = [u for u in dict.fromkeys(users or []) if u and u not in ("Guest",)]
	fn = _dispatch("recipients_for_users")
	if not fn or not users:
		return []
	return fn(users) or []


def approver_recipients(role: str, hostel: str | None = None) -> list[dict]:
	fn = _dispatch("recipients_for_approvers")
	if not fn:
		return []
	return fn(role, hostel=hostel) or []


def get_role_holders(role: str) -> list[str]:
	"""Enabled system users holding a role."""
	return frappe.db.sql_list(
		"""
		select distinct u.name
		from `tabUser` u
		inner join `tabHas Role` r on r.parent = u.name and r.parenttype = 'User'
		where r.role = %(role)s and u.enabled = 1 and u.user_type = 'System User'
			and u.name not in ('Administrator', 'Guest')
		""",
		{"role": role},
	)


def get_mess_student_users(mess: str) -> list[str]:
	"""Portal users of active, mess-enrolled students living in the hostels the mess serves."""
	hostels = get_mess_hostels(mess)
	if not hostels:
		return []
	return frappe.get_all(
		"Hostel Student",
		filters={
			"current_hostel": ["in", hostels],
			"status": "Active",
			"mess_enrolled": 1,
			"user": ["is", "set"],
		},
		pluck="user",
	)


def get_institution() -> dict:
	"""Institution letterhead details, or an empty dict before Hostel Settings exists."""
	from hostel360.utils.jinja import get_institution_details

	try:
		return frappe._dict(get_institution_details() or {})
	except frappe.DoesNotExistError:
		return frappe._dict()
