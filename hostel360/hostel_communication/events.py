"""Catalogue of messaging events.

Every module that talks to students, parents or staff calls
``hostel360.hostel_communication.dispatch.notify`` with one of these event names and a
context dict. Message Templates are keyed on ``{event}-{channel}``; the variables listed
in ``EVENT_CONTEXT`` are the ones the default templates use, so callers should supply
all of them (missing values render as empty text rather than failing).

Variables available to every template, filled in by the dispatcher:
``recipient_name``, ``recipient_kind`` ("student" | "parent" | "staff" | ""),
``institution_name``, ``institution_short_name``, ``portal_url``, ``portal_domain``,
``support_phone``, ``support_email`` and ``site_url``.

Helpers available to every template: ``rupees(amount)`` ("Rs.1,250", plain ASCII so
SMS stays in the 160-character GSM alphabet), ``format_inr(amount)`` ("₹1,250.00"),
``fdate(value)``, ``fdatetime(value)`` and ``short(text, length)``.
"""

# Channels
SMS = "SMS"
WHATSAPP = "WhatsApp"
EMAIL = "Email"
IN_APP = "In-App"

CHANNELS = (SMS, WHATSAPP, EMAIL, IN_APP)
MOBILE_CHANNELS = (SMS, WHATSAPP)

# Events
OTP_LOGIN = "otp_login"
STUDENT_VALIDATED = "student_validated"
ROOM_ALLOTTED = "room_allotted"
FEE_DUE_PUBLISHED = "fee_due_published"
FEE_REMINDER = "fee_reminder"
PAYMENT_RECEIVED = "payment_received"
MESS_BILL_PUBLISHED = "mess_bill_published"
MENU_PUBLISHED = "menu_published"
MESS_REDUCTION_SUBMITTED = "mess_reduction_submitted"
MESS_REDUCTION_DECIDED = "mess_reduction_decided"
LEAVE_SUBMITTED = "leave_submitted"
LEAVE_PARENT_APPROVAL_REQUIRED = "leave_parent_approval_required"
LEAVE_WARDEN_APPROVAL_REQUIRED = "leave_warden_approval_required"
LEAVE_DECIDED = "leave_decided"
LEAVE_OVERDUE = "leave_overdue"
GRIEVANCE_RECEIVED = "grievance_received"
GRIEVANCE_ASSIGNED = "grievance_assigned"
GRIEVANCE_UPDATED = "grievance_updated"
GRIEVANCE_ESCALATED = "grievance_escalated"
GRIEVANCE_RESOLVED = "grievance_resolved"
BONAFIDE_DECIDED = "bonafide_decided"
SUPPORT_TICKET_UPDATE = "support_ticket_update"
MESS_LOW_STOCK = "mess_low_stock"

# Not a template event: used on Message Log rows written by ``send_direct`` (operations
# alerts and test messages), which bypass templates entirely.
DIRECT_MESSAGE = "direct_message"

EVENTS = (
	OTP_LOGIN,
	STUDENT_VALIDATED,
	ROOM_ALLOTTED,
	FEE_DUE_PUBLISHED,
	FEE_REMINDER,
	PAYMENT_RECEIVED,
	MESS_BILL_PUBLISHED,
	MENU_PUBLISHED,
	MESS_REDUCTION_SUBMITTED,
	MESS_REDUCTION_DECIDED,
	LEAVE_SUBMITTED,
	LEAVE_PARENT_APPROVAL_REQUIRED,
	LEAVE_WARDEN_APPROVAL_REQUIRED,
	LEAVE_DECIDED,
	LEAVE_OVERDUE,
	GRIEVANCE_RECEIVED,
	GRIEVANCE_ASSIGNED,
	GRIEVANCE_UPDATED,
	GRIEVANCE_ESCALATED,
	GRIEVANCE_RESOLVED,
	BONAFIDE_DECIDED,
	SUPPORT_TICKET_UPDATE,
	MESS_LOW_STOCK,
)

EVENT_LABELS = {
	OTP_LOGIN: "Portal sign-in code",
	STUDENT_VALIDATED: "Student registration verified",
	ROOM_ALLOTTED: "Room allotted",
	FEE_DUE_PUBLISHED: "Hostel fee due",
	FEE_REMINDER: "Fee payment reminder",
	PAYMENT_RECEIVED: "Payment received",
	MESS_BILL_PUBLISHED: "Mess bill published",
	MENU_PUBLISHED: "Mess menu published",
	MESS_REDUCTION_SUBMITTED: "Mess reduction application submitted",
	MESS_REDUCTION_DECIDED: "Mess reduction decision",
	LEAVE_SUBMITTED: "Leave application submitted",
	LEAVE_PARENT_APPROVAL_REQUIRED: "Leave awaiting parent approval",
	LEAVE_WARDEN_APPROVAL_REQUIRED: "Leave awaiting warden approval",
	LEAVE_DECIDED: "Leave decision",
	LEAVE_OVERDUE: "Overdue return from leave",
	GRIEVANCE_RECEIVED: "Grievance received",
	GRIEVANCE_ASSIGNED: "Grievance assigned",
	GRIEVANCE_UPDATED: "Grievance updated",
	GRIEVANCE_ESCALATED: "Grievance escalated",
	GRIEVANCE_RESOLVED: "Grievance resolved",
	BONAFIDE_DECIDED: "Bonafide certificate decision",
	SUPPORT_TICKET_UPDATE: "Support ticket update",
	MESS_LOW_STOCK: "Mess stock running low",
}

# Variables the default templates use for each event. Callers should pass all of them;
# a missing one renders as empty text. Several have aliases the templates also accept
# (``outstanding_amount`` for ``outstanding``, ``update`` or ``comment`` for ``message``,
# ``remarks`` for ``warden_remarks``), and ``portal_link`` / ``desk_link``, when given,
# become the button links in e-mails.
EVENT_CONTEXT = {
	OTP_LOGIN: ("otp", "expiry_minutes"),
	STUDENT_VALIDATED: ("student_name", "register_number"),
	ROOM_ALLOTTED: ("student_name", "hostel_name", "room", "room_number", "bed", "bed_label", "from_date"),
	FEE_DUE_PUBLISHED: ("student_name", "invoice", "invoice_type", "amount", "due_date"),
	FEE_REMINDER: ("student_name", "invoice", "outstanding", "due_date", "is_overdue", "days_overdue"),
	PAYMENT_RECEIVED: ("student_name", "receipt_number", "amount", "payment_date", "mode_of_payment"),
	MESS_BILL_PUBLISHED: (
		"student_name",
		"mess",
		"period",
		"amount",
		"due_date",
		"chargeable_days",
		"daily_rate",
		"invoice",
	),
	MENU_PUBLISHED: ("mess", "week_start", "week_end"),
	MESS_REDUCTION_SUBMITTED: (
		"student_name",
		"application",
		"from_date",
		"to_date",
		"number_of_days",
		"reason",
	),
	MESS_REDUCTION_DECIDED: (
		"student_name",
		"application",
		"status",
		"from_date",
		"to_date",
		"number_of_days",
		"remarks",
	),
	LEAVE_SUBMITTED: ("student_name", "leave", "from_datetime", "to_datetime", "destination", "status"),
	LEAVE_PARENT_APPROVAL_REQUIRED: (
		"student_name",
		"leave",
		"from_datetime",
		"to_datetime",
		"destination",
		"reason",
	),
	LEAVE_WARDEN_APPROVAL_REQUIRED: (
		"student_name",
		"leave",
		"hostel",
		"from_datetime",
		"to_datetime",
		"destination",
		"reason",
	),
	LEAVE_DECIDED: (
		"student_name",
		"leave",
		"status",
		"from_datetime",
		"to_datetime",
		"warden_remarks",
		"parent_remarks",
	),
	LEAVE_OVERDUE: ("student_name", "leave", "hostel", "to_datetime", "destination"),
	GRIEVANCE_RECEIVED: ("grievance", "student_name", "subject", "category", "priority", "hostel"),
	GRIEVANCE_ASSIGNED: ("grievance", "subject", "category", "priority", "hostel", "sla_due_by", "message"),
	GRIEVANCE_UPDATED: ("grievance", "subject", "status", "message"),
	GRIEVANCE_ESCALATED: ("grievance", "subject", "category", "priority", "escalation_level", "hostel"),
	GRIEVANCE_RESOLVED: ("grievance", "subject", "resolution"),
	BONAFIDE_DECIDED: (
		"student_name",
		"request",
		"purpose",
		"status",
		"certificate_number",
		"valid_till",
		"rejection_reason",
	),
	SUPPORT_TICKET_UPDATE: ("ticket", "subject", "status", "previous_status", "comment", "resolution"),
	MESS_LOW_STOCK: ("mess", "item_count", "items_summary", "items"),
}

# Events that must never be sent over e-mail or the in-app inbox.
MOBILE_ONLY_EVENTS = frozenset({OTP_LOGIN})

# Representative values used to preview templates and to validate them on save.
_SAMPLE_VALUES = {
	"otp": "482913",
	"expiry_minutes": 5,
	"student_name": "Karthikeyan R",
	"register_number": "2026BE1042",
	"hostel_name": "Boys Hostel 1",
	"hostel": "BH1",
	"room": "BH1-204",
	"room_number": "204",
	"bed": "BH1-204-B",
	"bed_label": "B",
	"from_date": "2026-07-01",
	"to_date": "2026-07-05",
	"invoice": "INV-2026-00123",
	"amount": 45250,
	"outstanding": 12500,
	"invoice_type": "Hostel Fee",
	"days_overdue": 0,
	"due_date": "2026-07-15",
	"is_overdue": 0,
	"receipt_number": "RCPT-2026-00456",
	"payment_date": "2026-07-10",
	"mode_of_payment": "UPI",
	"period": "June 2026",
	"chargeable_days": 27,
	"daily_rate": 118.5,
	"mess": "Main Mess",
	"week_start": "2026-07-06",
	"week_end": "2026-07-12",
	"application": "MRA-2026-00031",
	"number_of_days": 5,
	"reason": "Home Visit",
	"status": "Approved",
	"remarks": "Have a safe journey",
	"warden_remarks": "Have a safe journey",
	"parent_remarks": "",
	"leave": "LV-2026-00078",
	"from_datetime": "2026-07-04 16:00:00",
	"to_datetime": "2026-07-07 19:00:00",
	"destination": "Madurai",
	"grievance": "GRV-2026-00019",
	"subject": "Fan not working in room",
	"category": "Electrical",
	"priority": "Medium",
	"sla_due_by": "2026-07-06 10:00:00",
	"message": "An electrician will visit your room this evening.",
	"escalation_level": 1,
	"resolution": "The fan regulator was replaced.",
	"request": "BON-2026-00011",
	"certificate_number": "BC/2026/00011",
	"purpose": "Bank Loan",
	"valid_till": "2026-10-08",
	"rejection_reason": "",
	"ticket": "TKT-2026-00007",
	"previous_status": "Open",
	"comment": "We have identified the cause and a fix is being deployed.",
	"item_count": 3,
	"items_summary": "Rice, Toor Dal, Cooking Oil",
	"items": [
		{"item": "Rice", "qty": 40, "uom": "Kg", "reorder_level": 100},
		{"item": "Toor Dal", "qty": 8, "uom": "Kg", "reorder_level": 25},
		{"item": "Cooking Oil", "qty": 12, "uom": "Litre", "reorder_level": 30},
	],
}


def get_sample_context(event: str) -> dict:
	"""Plausible values for every variable the event expects (for previews and validation)."""
	return {key: _SAMPLE_VALUES.get(key, "") for key in EVENT_CONTEXT.get(event, ())}


def is_valid_event(event: str | None) -> bool:
	return event in EVENT_LABELS


def normalize_channel(channel: str | None) -> str | None:
	"""Map loose spellings ("sms", "whatsapp", "in_app", "inapp") to channel names."""
	if not channel:
		return None
	key = str(channel).strip().lower().replace("_", "-").replace(" ", "-")
	return {
		"sms": SMS,
		"whatsapp": WHATSAPP,
		"whats-app": WHATSAPP,
		"email": EMAIL,
		"e-mail": EMAIL,
		"in-app": IN_APP,
		"inapp": IN_APP,
		"app": IN_APP,
	}.get(key)
