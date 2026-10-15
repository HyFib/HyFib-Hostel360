"""Default wording for every messaging event.

SMS bodies are plain ASCII (no rupee symbol, no smart quotes) so they stay in the GSM
alphabet and fit one 160-character segment with typical values. They contain no Jinja
conditionals: DLT scrubbing compares each SMS with the registered template, so the text
outside the variables must never change. Register each SMS body on the DLT portal with
every ``{{ ... }}`` replaced by ``{#var#}``.

WhatsApp bodies mirror the template to be submitted in WhatsApp Manager, with the
``template_parameters`` lines supplying {{1}}, {{2}}, ... in order of appearance.

E-mail and in-app messages may use conditionals; their values are HTML-escaped before
rendering. Some variables have aliases because modules describe the same thing
differently (see ``events.EVENT_CONTEXT``).
"""

from hostel360.hostel_communication import events as ev

SIGN_OFF = " -{{ institution_short_name }} Hostel"

# Expressions shared by several templates.
ROOM = "room_number or room"
BED = "bed_label or bed"
OUTSTANDING = "outstanding or outstanding_amount"
LEAVE_REMARKS = "warden_remarks or parent_remarks or remarks"
GRIEVANCE_MESSAGE = "message or update or comment"
TICKET_UPDATE = "comment or resolution or message or update"

_BUTTON = (
	'<p style="margin:24px 0"><a href="{{ %s }}" '
	'style="display:inline-block;padding:10px 20px;background:#1d4ed8;color:#ffffff;'
	'text-decoration:none;border-radius:6px;font-weight:600">%s</a></p>'
)

_FOOTER = (
	'<p style="margin-top:24px">Regards,<br>{{ institution_short_name }} Hostel Office</p>\n'
	'<p style="font-size:12px;color:#6b7280">Need help? Call {{ support_phone }} or write to '
	"{{ support_email }}. This is an automated message from the hostel portal.</p>"
)

_STAFF_FOOTER = (
	'<p style="margin-top:24px">Hostel360</p>\n'
	'<p style="font-size:12px;color:#6b7280">You are receiving this because of your role in '
	"the hostel office. This is an automated message.</p>"
)


def _rows(*pairs) -> str:
	cells = "".join(
		'<tr><td style="padding:6px 12px 6px 0;color:#6b7280">%s</td>'
		'<td style="padding:6px 0;font-weight:600">%s</td></tr>' % pair
		for pair in pairs
	)
	return '<table style="border-collapse:collapse;margin:12px 0">%s</table>' % cells


def _quote(expression: str) -> str:
	return (
		'<blockquote style="margin:12px 0;padding:8px 12px;border-left:3px solid #d1d5db">'
		"{{ %s }}</blockquote>" % expression
	)


def _email(*paragraphs, action: str | None = None, greeting: str = "Student", staff: bool = False) -> str:
	parts = ['<p>Dear {{ recipient_name or "%s" }},</p>' % greeting]
	for paragraph in paragraphs:
		parts.append(paragraph if paragraph.startswith("<") else f"<p>{paragraph}</p>")
	if action:
		link = "desk_link or site_url" if staff else "portal_link or portal_url"
		parts.append(_BUTTON % (link, action))
	parts.append(_STAFF_FOOTER if staff else _FOOTER)
	return "\n".join(parts)


def _wa(name: str) -> str:
	return f"h360_{name}"


TEMPLATES = {
	ev.OTP_LOGIN: {
		ev.SMS: {
			"body": "{{ otp }} is your OTP to sign in to the {{ institution_short_name }} Hostel portal. "
			"It is valid for {{ expiry_minutes }} minutes. Do not share it with anyone."
			"{% if portal_domain %}\n\n@{{ portal_domain }} #{{ otp }}{% endif %}",
			"template_parameters": "otp\nexpiry_minutes",
		},
		ev.WHATSAPP: {
			"body": "{{ otp }} is your verification code for the {{ institution_short_name }} Hostel "
			"portal. For your security, do not share this code.",
			"template_parameters": "otp",
		},
	},
	ev.STUDENT_VALIDATED: {
		ev.SMS: {
			"body": "Dear {{ short(student_name) }}, your hostel registration {{ register_number }} is "
			"verified. All portal services are now open at {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "short(student_name)\nregister_number",
		},
		ev.WHATSAPP: {
			"body": "Dear {{ student_name }}, your hostel registration {{ register_number }} has been "
			"verified by the hostel office. You can now use all services on the hostel portal: "
			"{{ portal_url }}",
			"template_parameters": "student_name\nregister_number\nportal_url",
		},
		ev.EMAIL: {
			"subject": "Your hostel registration is verified",
			"body": _email(
				"Your hostel registration (Register No. <strong>{{ register_number }}</strong>) has been "
				"verified by the hostel office.",
				"You can now use every service on the hostel portal: pay fees online, see the mess "
				"menu, apply for leave or mess reduction, raise grievances and request certificates.",
				action="Open the hostel portal",
			),
		},
		ev.IN_APP: {
			"subject": "Your registration is verified",
			"body": "Register No. {{ register_number }} has been verified. All portal services are now "
			"available to you.",
		},
	},
	ev.ROOM_ALLOTTED: {
		ev.SMS: {
			"body": "Room allotted to {{ short(student_name) }}: {{ short(hostel_name) }}, room "
			"{{ %s }}, bed {{ %s }}, from {{ fdate(from_date) }}. Details at {{ portal_url }}"
			% (ROOM, BED)
			+ SIGN_OFF,
			"template_parameters": f"short(student_name)\nshort(hostel_name)\n{ROOM}\n{BED}\nfdate(from_date)",
		},
		ev.WHATSAPP: {
			"body": "Room allotted to {{ student_name }}: {{ hostel_name }}, room {{ %s }}, bed {{ %s }}, "
			"from {{ fdate(from_date) }}. Please report to the hostel office with your ID card to "
			"collect the room key." % (ROOM, BED),
			"template_parameters": f"student_name\nhostel_name\n{ROOM}\n{BED}\nfdate(from_date)",
		},
		ev.EMAIL: {
			"subject": "Room allotted: {{ hostel_name }}, room {{ %s }}" % ROOM,
			"body": _email(
				"A hostel room has been allotted to {{ student_name }}.",
				_rows(
					("Hostel", "{{ hostel_name }}"),
					("Room", "{{ %s }}" % ROOM),
					("Bed", "{{ %s }}" % BED),
					("From", "{{ fdate(from_date) }}"),
				),
				"Please report to the hostel office with your college ID card to collect the room key.",
				action="View room details",
			),
		},
		ev.IN_APP: {
			"subject": "Room {{ %s }} allotted" % ROOM,
			"body": "{{ hostel_name }}, room {{ %s }}, bed {{ %s }} from {{ fdate(from_date) }}."
			% (ROOM, BED),
		},
	},
	ev.FEE_DUE_PUBLISHED: {
		ev.SMS: {
			"body": "Hostel fee of {{ rupees(amount) }} for {{ short(student_name) }} is due on "
			"{{ fdate(due_date) }} (Invoice {{ invoice }}). Pay online at {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "rupees(amount)\nshort(student_name)\nfdate(due_date)\ninvoice",
		},
		ev.WHATSAPP: {
			"body": "Dear {{ recipient_name }}, a hostel fee of {{ rupees(amount) }} for "
			"{{ student_name }} is due on {{ fdate(due_date) }} (Invoice {{ invoice }}). You can pay "
			"securely by UPI, card or net banking on the hostel portal: {{ portal_url }}",
			"template_parameters": "recipient_name\nrupees(amount)\nstudent_name\nfdate(due_date)\n"
			"invoice\nportal_url",
		},
		ev.EMAIL: {
			"subject": "Hostel fee due: {{ format_inr(amount) }} by {{ fdate(due_date) }}",
			"body": _email(
				"A hostel fee has been published for {{ student_name }}.",
				_rows(
					("Description", "{{ invoice_type or 'Hostel fee' }}"),
					("Invoice", "{{ invoice }}"),
					("Amount", "{{ format_inr(amount) }}"),
					("Due date", "{{ fdate(due_date) }}"),
				),
				"You can pay online by UPI, debit or credit card, or net banking. The receipt is "
				"available on the portal as soon as the payment is confirmed.",
				action="Pay now",
			),
		},
		ev.IN_APP: {
			"subject": "Fee due: {{ format_inr(amount) }} by {{ fdate(due_date) }}",
			"body": "{{ invoice_type or 'Hostel fee' }} for {{ student_name }} (invoice {{ invoice }}). "
			"You can pay online from the Fees page.",
		},
	},
	ev.FEE_REMINDER: {
		ev.SMS: {
			"body": "Reminder: hostel fee of {{ rupees(%s) }} for {{ short(student_name) }} (due "
			"{{ fdate(due_date) }}) is pending. Please pay at {{ portal_url }}" % OUTSTANDING + SIGN_OFF,
			"template_parameters": f"rupees({OUTSTANDING})\nshort(student_name)\nfdate(due_date)",
		},
		ev.WHATSAPP: {
			"body": "Dear {{ recipient_name }}, this is a gentle reminder that {{ rupees(%s) }} is pending "
			"on invoice {{ invoice }} for {{ student_name }}, due on {{ fdate(due_date) }}. Please pay on "
			"the hostel portal: {{ portal_url }}. Ignore this message if you have already paid."
			% OUTSTANDING,
			"template_parameters": f"recipient_name\nrupees({OUTSTANDING})\ninvoice\nstudent_name\n"
			"fdate(due_date)\nportal_url",
		},
		ev.EMAIL: {
			"subject": "{% if is_overdue %}Overdue{% else %}Reminder{% endif %}: hostel fee of "
			"{{ format_inr(" + OUTSTANDING + ") }} for {{ student_name }}",
			"body": _email(
				"{% if is_overdue %}The hostel fee below was due on {{ fdate(due_date) }} and is still "
				"pending.{% else %}This is a gentle reminder that the hostel fee below falls due on "
				"{{ fdate(due_date) }}.{% endif %}",
				_rows(
					("Student", "{{ student_name }}"),
					("Invoice", "{{ invoice }}"),
					("Amount pending", "{{ format_inr(%s) }}" % OUTSTANDING),
					("Due date", "{{ fdate(due_date) }}"),
				),
				"If you have already paid, please ignore this message. Payments made in the last few "
				"minutes may take a short while to show.",
				action="Pay now",
			),
		},
		ev.IN_APP: {
			"subject": "{% if is_overdue %}Overdue{% else %}Due soon{% endif %}: "
			"{{ format_inr(" + OUTSTANDING + ") }}",
			"body": "Invoice {{ invoice }} for {{ student_name }} {% if is_overdue %}was{% else %}is{% endif %} "
			"due on {{ fdate(due_date) }}.",
		},
	},
	ev.PAYMENT_RECEIVED: {
		ev.SMS: {
			"body": "Payment of {{ rupees(amount) }} received for {{ short(student_name) }} on "
			"{{ fdate(payment_date) }}. Receipt No. {{ receipt_number }}. Thank you." + SIGN_OFF,
			"template_parameters": "rupees(amount)\nshort(student_name)\nfdate(payment_date)\nreceipt_number",
		},
		ev.WHATSAPP: {
			"body": "Thank you. We have received {{ rupees(amount) }} for {{ student_name }} on "
			"{{ fdate(payment_date) }} by {{ mode_of_payment }}. Receipt No. {{ receipt_number }}. "
			"You can download the receipt from the hostel portal.",
			"template_parameters": "rupees(amount)\nstudent_name\nfdate(payment_date)\nmode_of_payment\n"
			"receipt_number",
		},
		ev.EMAIL: {
			"subject": "Payment received: {{ format_inr(amount) }} (Receipt {{ receipt_number }})",
			"body": _email(
				"Thank you. We have received the following payment for {{ student_name }}.",
				_rows(
					("Receipt No.", "{{ receipt_number }}"),
					("Amount", "{{ format_inr(amount) }}"),
					("Date", "{{ fdate(payment_date) }}"),
					("Mode", "{{ mode_of_payment }}"),
				),
				"You can download the receipt at any time from the Receipts page of the hostel portal.",
				action="Download receipt",
			),
		},
		ev.IN_APP: {
			"subject": "Payment received: {{ format_inr(amount) }}",
			"body": "Receipt {{ receipt_number }} dated {{ fdate(payment_date) }} ({{ mode_of_payment }}).",
		},
	},
	ev.MESS_BILL_PUBLISHED: {
		ev.SMS: {
			"body": "Mess bill for {{ period }}: {{ rupees(amount) }} for {{ short(student_name) }} "
			"({{ chargeable_days }} days), due {{ fdate(due_date) }}. Pay at {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "period\nrupees(amount)\nshort(student_name)\nchargeable_days\nfdate(due_date)",
		},
		ev.WHATSAPP: {
			"body": "Dear {{ recipient_name }}, the mess bill for {{ period }} is {{ rupees(amount) }} for "
			"{{ student_name }} ({{ chargeable_days }} days at {{ rupees(daily_rate) }} per day). Please "
			"pay by {{ fdate(due_date) }} on the hostel portal: {{ portal_url }}",
			"template_parameters": "recipient_name\nperiod\nrupees(amount)\nstudent_name\nchargeable_days\n"
			"rupees(daily_rate)\nfdate(due_date)\nportal_url",
		},
		ev.EMAIL: {
			"subject": "Mess bill for {{ period }}: {{ format_inr(amount) }}",
			"body": _email(
				"The mess bill for {{ period }} has been published for {{ student_name }}.",
				_rows(
					("Days charged", "{{ chargeable_days }}"),
					("Rate per day", "{{ format_inr(daily_rate) }}"),
					("Amount", "{{ format_inr(amount) }}"),
					("Invoice", "{{ invoice }}"),
					("Due date", "{{ fdate(due_date) }}"),
				),
				"Days covered by an approved mess reduction are not charged. The detailed bill is "
				"available on the Mess Bills page.",
				action="View and pay",
			),
		},
		ev.IN_APP: {
			"subject": "Mess bill for {{ period }}: {{ format_inr(amount) }}",
			"body": "{{ chargeable_days }} days at {{ format_inr(daily_rate) }} per day. Due on "
			"{{ fdate(due_date) }}.",
		},
	},
	ev.MENU_PUBLISHED: {
		ev.SMS: {
			"body": "The {{ short(mess) }} menu for {{ fdate(week_start) }} to {{ fdate(week_end) }} is "
			"now available at {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "short(mess)\nfdate(week_start)\nfdate(week_end)",
		},
		ev.WHATSAPP: {
			"body": "The {{ mess }} menu for the week of {{ fdate(week_start) }} to {{ fdate(week_end) }} "
			"has been published. See what is cooking on the hostel portal: {{ portal_url }}",
			"template_parameters": "mess\nfdate(week_start)\nfdate(week_end)\nportal_url",
		},
		ev.EMAIL: {
			"subject": "Mess menu for {{ fdate(week_start) }} to {{ fdate(week_end) }}",
			"body": _email(
				"The {{ mess }} menu for the week of {{ fdate(week_start) }} to {{ fdate(week_end) }} "
				"has been published.",
				action="See this week's menu",
			),
		},
		ev.IN_APP: {
			"subject": "This week's menu is out",
			"body": "{{ mess }}: {{ fdate(week_start) }} to {{ fdate(week_end) }}.",
		},
	},
	ev.MESS_REDUCTION_SUBMITTED: {
		ev.SMS: {
			"body": "Mess reduction {{ application }} for {{ short(student_name) }}, {{ fdate(from_date) }} "
			"to {{ fdate(to_date) }} ({{ number_of_days }} days), is submitted for approval." + SIGN_OFF,
			"template_parameters": "application\nshort(student_name)\nfdate(from_date)\nfdate(to_date)\n"
			"number_of_days",
		},
		ev.WHATSAPP: {
			"body": "Mess reduction application {{ application }} for {{ student_name }} from "
			"{{ fdate(from_date) }} to {{ fdate(to_date) }} ({{ number_of_days }} days, reason: "
			"{{ reason }}) has been submitted for approval.",
			"template_parameters": "application\nstudent_name\nfdate(from_date)\nfdate(to_date)\n"
			"number_of_days\nreason",
		},
		ev.EMAIL: {
			"subject": "Mess reduction application {{ application }} submitted",
			"body": _email(
				"A mess reduction application for {{ student_name }} is waiting for approval.",
				_rows(
					("Application", "{{ application }}"),
					("From", "{{ fdate(from_date) }}"),
					("To", "{{ fdate(to_date) }}"),
					("Days", "{{ number_of_days }}"),
					("Reason", "{{ reason }}"),
				),
				"You can approve or reject it from the Approvals page in Hostel360.",
				action="Review application",
				greeting="Colleague",
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "Mess reduction to review: {{ student_name }}",
			"body": "{{ application }}: {{ fdate(from_date) }} to {{ fdate(to_date) }}, "
			"{{ number_of_days }} days ({{ reason }}).",
		},
	},
	ev.MESS_REDUCTION_DECIDED: {
		ev.SMS: {
			"body": "Mess reduction {{ application }} for {{ short(student_name) }} ({{ fdate(from_date) }} "
			"to {{ fdate(to_date) }}) has been {{ status|lower }}." + SIGN_OFF,
			"template_parameters": "application\nshort(student_name)\nfdate(from_date)\nfdate(to_date)\n"
			"status|lower",
		},
		ev.WHATSAPP: {
			"body": "Mess reduction application {{ application }} for {{ student_name }} from "
			"{{ fdate(from_date) }} to {{ fdate(to_date) }} has been {{ status|lower }}. Remarks: "
			"{{ remarks or 'None' }}",
			"template_parameters": "application\nstudent_name\nfdate(from_date)\nfdate(to_date)\nstatus|lower\n"
			"remarks or 'None'",
		},
		ev.EMAIL: {
			"subject": "Mess reduction {{ application }} {{ status|lower }}",
			"body": _email(
				"The mess reduction application {{ application }} for {{ student_name }} "
				"({{ fdate(from_date) }} to {{ fdate(to_date) }}, {{ number_of_days }} days) has been "
				"<strong>{{ status|lower }}</strong>.",
				"{% if remarks %}Remarks: {{ remarks }}{% else %}No remarks were added.{% endif %}",
				"{% if status == 'Approved' %}These days will not be charged in the mess bill.{% else %}"
				"Please contact the mess office if you have any questions.{% endif %}",
				action="View details",
			),
		},
		ev.IN_APP: {
			"subject": "Mess reduction {{ status|lower }}",
			"body": "{{ application }}: {{ fdate(from_date) }} to {{ fdate(to_date) }}."
			"{% if remarks %} Remarks: {{ remarks }}{% endif %}",
		},
	},
	ev.LEAVE_SUBMITTED: {
		ev.SMS: {
			"body": "Leave request {{ leave }} for {{ short(student_name) }} from "
			"{{ fdatetime(from_datetime) }} to {{ fdatetime(to_datetime) }} is submitted. Status: "
			"{{ status }}." + SIGN_OFF,
			"template_parameters": "leave\nshort(student_name)\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)\nstatus",
		},
		ev.WHATSAPP: {
			"body": "Leave request {{ leave }} for {{ student_name }} to {{ destination }} from "
			"{{ fdatetime(from_datetime) }} to {{ fdatetime(to_datetime) }} has been submitted. "
			"Current status: {{ status }}.",
			"template_parameters": "leave\nstudent_name\ndestination\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)\nstatus",
		},
		ev.EMAIL: {
			"subject": "Leave request {{ leave }} submitted",
			"body": _email(
				"A home visit leave request has been submitted for {{ student_name }}.",
				_rows(
					("Request", "{{ leave }}"),
					("Destination", "{{ destination }}"),
					("Leaving", "{{ fdatetime(from_datetime) }}"),
					("Returning", "{{ fdatetime(to_datetime) }}"),
					("Status", "{{ status }}"),
				),
				"You will be informed at each step of the approval.",
				action="Track the request",
			),
		},
		ev.IN_APP: {
			"subject": "Leave request submitted",
			"body": "{{ leave }}: {{ fdatetime(from_datetime) }} to {{ fdatetime(to_datetime) }}. "
			"Status: {{ status }}.",
		},
	},
	ev.LEAVE_PARENT_APPROVAL_REQUIRED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} has requested leave to {{ short(destination, 20) }} from "
			"{{ fdatetime(from_datetime) }} to {{ fdatetime(to_datetime) }}. Please respond at "
			"{{ portal_url }}" + SIGN_OFF,
			"template_parameters": "short(student_name)\nshort(destination, 20)\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)",
		},
		ev.WHATSAPP: {
			"body": "Dear {{ recipient_name }}, {{ student_name }} has requested leave to {{ destination }} "
			"from {{ fdatetime(from_datetime) }} to {{ fdatetime(to_datetime) }}. Reason: {{ reason }}. "
			"Please approve or reject the request on the hostel portal: {{ portal_url }}",
			"template_parameters": "recipient_name\nstudent_name\ndestination\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)\nreason\nportal_url",
		},
		ev.EMAIL: {
			"subject": "Please review a leave request from {{ student_name }}",
			"body": _email(
				"{{ student_name }} has requested home visit leave and needs your approval before the "
				"warden reviews it.",
				_rows(
					("Destination", "{{ destination }}"),
					("Leaving", "{{ fdatetime(from_datetime) }}"),
					("Returning", "{{ fdatetime(to_datetime) }}"),
					("Reason", "{{ reason }}"),
				),
				"If you did not expect this request, please call the hostel office.",
				action="Approve or reject",
				greeting="Parent",
			),
		},
		ev.IN_APP: {
			"subject": "Leave request awaiting your approval",
			"body": "{{ student_name }}: {{ destination }}, {{ fdatetime(from_datetime) }} to "
			"{{ fdatetime(to_datetime) }}.",
		},
	},
	ev.LEAVE_WARDEN_APPROVAL_REQUIRED: {
		ev.SMS: {
			"body": "Leave {{ leave }} of {{ short(student_name) }} ({{ hostel }}), "
			"{{ fdatetime(from_datetime) }} to {{ fdatetime(to_datetime) }}, awaits your approval."
			+ SIGN_OFF,
			"template_parameters": "leave\nshort(student_name)\nhostel\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)",
		},
		ev.WHATSAPP: {
			"body": "Leave request {{ leave }} of {{ student_name }} ({{ hostel }}) to {{ destination }} "
			"from {{ fdatetime(from_datetime) }} to {{ fdatetime(to_datetime) }} is awaiting your "
			"approval.",
			"template_parameters": "leave\nstudent_name\nhostel\ndestination\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)",
		},
		ev.EMAIL: {
			"subject": "Leave approval needed: {{ student_name }} ({{ leave }})",
			"body": _email(
				"The following leave request is waiting for your approval.",
				_rows(
					("Student", "{{ student_name }}"),
					("Hostel", "{{ hostel }}"),
					("Destination", "{{ destination }}"),
					("Leaving", "{{ fdatetime(from_datetime) }}"),
					("Returning", "{{ fdatetime(to_datetime) }}"),
					("Reason", "{{ reason }}"),
				),
				"You can approve or reject it from the Approvals page in Hostel360.",
				action="Review the request",
				greeting="Warden",
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "Leave approval needed: {{ student_name }}",
			"body": "{{ leave }} ({{ hostel }}): {{ fdatetime(from_datetime) }} to "
			"{{ fdatetime(to_datetime) }}, {{ destination }}.",
		},
	},
	ev.LEAVE_DECIDED: {
		ev.SMS: {
			"body": "Leave {{ leave }} for {{ short(student_name) }} from {{ fdatetime(from_datetime) }} "
			"to {{ fdatetime(to_datetime) }} has been {{ status|lower }}." + SIGN_OFF,
			"template_parameters": "leave\nshort(student_name)\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)\nstatus|lower",
		},
		ev.WHATSAPP: {
			"body": "Leave request {{ leave }} for {{ student_name }} from {{ fdatetime(from_datetime) }} "
			"to {{ fdatetime(to_datetime) }} has been {{ status|lower }}. Remarks: "
			"{{ %s or 'None' }}" % LEAVE_REMARKS,
			"template_parameters": "leave\nstudent_name\nfdatetime(from_datetime)\nfdatetime(to_datetime)\n"
			f"status|lower\n{LEAVE_REMARKS} or 'None'",
		},
		ev.EMAIL: {
			"subject": "Leave request {{ leave }} {{ status|lower }}",
			"body": _email(
				"The leave request {{ leave }} for {{ student_name }} ({{ fdatetime(from_datetime) }} to "
				"{{ fdatetime(to_datetime) }}) has been <strong>{{ status|lower }}</strong>.",
				"{%% if %s %%}Remarks: {{ %s }}{%% else %%}No remarks were added.{%% endif %%}"
				% (LEAVE_REMARKS, LEAVE_REMARKS),
				"{% if status == 'Approved' %}Please check out at the hostel gate when leaving and check "
				"in on your return.{% endif %}",
				action="View the request",
			),
		},
		ev.IN_APP: {
			"subject": "Leave {{ status|lower }}",
			"body": "{{ leave }}: {{ fdatetime(from_datetime) }} to {{ fdatetime(to_datetime) }}."
			"{%% if %s %%} Remarks: {{ %s }}{%% endif %%}" % (LEAVE_REMARKS, LEAVE_REMARKS),
		},
	},
	ev.LEAVE_OVERDUE: {
		ev.SMS: {
			"body": "{{ short(student_name) }} ({{ hostel }}) has not yet checked in from leave due back "
			"on {{ fdatetime(to_datetime) }}. Please contact the hostel office." + SIGN_OFF,
			"template_parameters": "short(student_name)\nhostel\nfdatetime(to_datetime)",
		},
		ev.WHATSAPP: {
			"body": "{{ student_name }} has not yet checked in to {{ hostel }} after leave {{ leave }}, "
			"which was due back on {{ fdatetime(to_datetime) }}. Please contact the hostel office at "
			"{{ support_phone }} if the return is delayed.",
			"template_parameters": "student_name\nhostel\nleave\nfdatetime(to_datetime)\nsupport_phone",
		},
		ev.EMAIL: {
			"subject": "Return from leave overdue: {{ student_name }}",
			"body": _email(
				"{{ student_name }} has not yet checked in to {{ hostel }} after leave {{ leave }} to "
				"{{ destination }}, which was due back on {{ fdatetime(to_datetime) }}.",
				"If the return is delayed for a genuine reason, please inform the hostel office so that "
				"the warden is aware.",
			),
		},
		ev.IN_APP: {
			"subject": "Return overdue: {{ student_name }}",
			"body": "Leave {{ leave }} ({{ hostel }}) was due back on {{ fdatetime(to_datetime) }}.",
		},
	},
	ev.GRIEVANCE_RECEIVED: {
		ev.SMS: {
			"body": "Grievance {{ grievance }} ({{ short(subject, 30) }}) has been received and will be "
			"attended to." + SIGN_OFF,
			"template_parameters": "grievance\nshort(subject, 30)",
		},
		ev.WHATSAPP: {
			"body": "Grievance {{ grievance }} has been received. Category: {{ category }}. Priority: "
			"{{ priority }}. Subject: {{ subject }}. Updates will be shared on the hostel portal.",
			"template_parameters": "grievance\ncategory\npriority\nsubject",
		},
		ev.EMAIL: {
			"subject": "Grievance {{ grievance }} received: {{ subject }}",
			"body": _email(
				"Grievance {{ grievance }} has been received.",
				_rows(
					("Student", "{{ student_name or 'Confidential' }}"),
					("Hostel", "{{ hostel }}"),
					("Subject", "{{ subject }}"),
					("Category", "{{ category }}"),
					("Priority", "{{ priority }}"),
				),
				"Updates will be shared as the hostel office works on it.",
				action="View grievance",
			),
		},
		ev.IN_APP: {
			"subject": "Grievance received: {{ subject }}",
			"body": "{{ grievance }} ({{ category }}, {{ priority }}){% if student_name %} from "
			"{{ student_name }}{% endif %}.",
		},
	},
	ev.GRIEVANCE_ASSIGNED: {
		ev.SMS: {
			"body": "Grievance {{ grievance }} ({{ category }}, {{ priority }}) is assigned to you: "
			"{{ short(subject, 25) }}. Please respond by {{ fdatetime(sla_due_by) }}." + SIGN_OFF,
			"template_parameters": "grievance\ncategory\npriority\nshort(subject, 25)\nfdatetime(sla_due_by)",
		},
		ev.WHATSAPP: {
			"body": "Grievance {{ grievance }} at {{ hostel }} has been assigned to you. Category: "
			"{{ category }}. Priority: {{ priority }}. Subject: {{ subject }}. Please respond by "
			"{{ fdatetime(sla_due_by) }}.",
			"template_parameters": "grievance\nhostel\ncategory\npriority\nsubject\nfdatetime(sla_due_by)",
		},
		ev.EMAIL: {
			"subject": "Grievance assigned to you: {{ subject }} ({{ grievance }})",
			"body": _email(
				"The following grievance has been assigned to you.",
				_rows(
					("Grievance", "{{ grievance }}"),
					("Hostel", "{{ hostel }}"),
					("Category", "{{ category }}"),
					("Priority", "{{ priority }}"),
					("Respond by", "{{ fdatetime(sla_due_by) }}"),
				),
				"{%% if %s %%}%s{%% endif %%}" % (GRIEVANCE_MESSAGE, _quote(GRIEVANCE_MESSAGE)),
				"Please update the grievance in Hostel360 as you make progress so the student is kept "
				"informed.",
				action="Open the grievance",
				greeting="Colleague",
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "Grievance assigned: {{ subject }}",
			"body": "{{ grievance }} ({{ category }}, {{ priority }}). Respond by "
			"{{ fdatetime(sla_due_by) }}.",
		},
	},
	ev.GRIEVANCE_UPDATED: {
		ev.SMS: {
			"body": "There is an update on grievance {{ grievance }} ({{ short(subject, 25) }}). Status: "
			"{{ status }}. View it at {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "grievance\nshort(subject, 25)\nstatus",
		},
		ev.WHATSAPP: {
			"body": "There is an update on grievance {{ grievance }} ({{ subject }}). Status: {{ status }}. "
			"Update: {{ %s or 'See the hostel portal' }}" % GRIEVANCE_MESSAGE,
			"template_parameters": f"grievance\nsubject\nstatus\n{GRIEVANCE_MESSAGE} or 'See the hostel portal'",
		},
		ev.EMAIL: {
			"subject": "Update on grievance {{ grievance }}: {{ subject }}",
			"body": _email(
				"There is an update on grievance {{ grievance }} ({{ subject }}).",
				"{%% if %s %%}%s{%% endif %%}" % (GRIEVANCE_MESSAGE, _quote(GRIEVANCE_MESSAGE)),
				"Current status: <strong>{{ status }}</strong>.",
				action="View grievance",
			),
		},
		ev.IN_APP: {
			"subject": "Grievance update: {{ subject }}",
			"body": "{{ %s or 'There is an update on your grievance.' }} (Status: {{ status }})"
			% GRIEVANCE_MESSAGE,
		},
	},
	ev.GRIEVANCE_ESCALATED: {
		ev.SMS: {
			"body": "Grievance {{ grievance }} ({{ category }}, {{ priority }}) at {{ hostel }} is "
			"escalated to level {{ escalation_level }}. Please review it today." + SIGN_OFF,
			"template_parameters": "grievance\ncategory\npriority\nhostel\nescalation_level",
		},
		ev.WHATSAPP: {
			"body": "Grievance {{ grievance }} at {{ hostel }} has been escalated to level "
			"{{ escalation_level }}. Category: {{ category }}. Priority: {{ priority }}. Subject: "
			"{{ subject }}. Please review it today.",
			"template_parameters": "grievance\nhostel\nescalation_level\ncategory\npriority\nsubject",
		},
		ev.EMAIL: {
			"subject": "Escalated grievance {{ grievance }}: {{ subject }}",
			"body": _email(
				"Grievance {{ grievance }} needs your attention and has been escalated to level "
				"{{ escalation_level }}.",
				_rows(
					("Hostel", "{{ hostel }}"),
					("Subject", "{{ subject }}"),
					("Category", "{{ category }}"),
					("Priority", "{{ priority }}"),
				),
				"Please review it today and update the student.",
				action="Open the grievance",
				greeting="Warden",
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "Grievance escalated: {{ subject }}",
			"body": "{{ grievance }} at {{ hostel }} is now at escalation level {{ escalation_level }}.",
		},
	},
	ev.GRIEVANCE_RESOLVED: {
		ev.SMS: {
			"body": "Your grievance {{ grievance }} ({{ short(subject, 25) }}) has been resolved. Please "
			"rate the resolution at {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "grievance\nshort(subject, 25)",
		},
		ev.WHATSAPP: {
			"body": "Grievance {{ grievance }} ({{ subject }}) has been resolved: {{ resolution or message }}. "
			"If the problem is not fixed, you can reopen it on the hostel portal: {{ portal_url }}",
			"template_parameters": "grievance\nsubject\nresolution or message\nportal_url",
		},
		ev.EMAIL: {
			"subject": "Grievance {{ grievance }} resolved: {{ subject }}",
			"body": _email(
				"Grievance {{ grievance }} ({{ subject }}) has been marked as resolved.",
				"{% if resolution or message %}" + _quote("resolution or message") + "{% endif %}",
				"Please take a moment to rate the resolution. If the problem is not fixed, you can "
				"reopen the grievance from the portal.",
				action="Rate the resolution",
			),
		},
		ev.IN_APP: {
			"subject": "Grievance resolved: {{ subject }}",
			"body": "{{ resolution or message }} Please rate the resolution, or reopen the grievance if "
			"the problem continues.",
		},
	},
	ev.BONAFIDE_DECIDED: {
		ev.SMS: {
			"body": "Bonafide certificate request {{ request }} for {{ short(student_name) }} has been "
			"{{ status|lower }}. Details at {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "request\nshort(student_name)\nstatus|lower",
		},
		ev.WHATSAPP: {
			"body": "Bonafide certificate request {{ request }} for {{ student_name }} has been "
			"{{ status|lower }}. {{ 'Certificate No. ' ~ certificate_number if certificate_number else "
			"'Reason: ' ~ (rejection_reason or 'Please contact the hostel office') }}.",
			"template_parameters": "request\nstudent_name\nstatus|lower\n"
			"'Certificate No. ' ~ certificate_number if certificate_number else 'Reason: ' ~ "
			"(rejection_reason or 'Please contact the hostel office')",
		},
		ev.EMAIL: {
			"subject": "Bonafide certificate request {{ request }} {{ status|lower }}",
			"body": _email(
				"The bonafide certificate request {{ request }} for {{ student_name }}"
				"{% if purpose %} ({{ purpose }}){% endif %} has been <strong>{{ status|lower }}</strong>.",
				"{% if certificate_number %}Certificate No. {{ certificate_number }} can be downloaded "
				"from the Certificates page of the portal{% if valid_till %} and is valid until "
				"{{ fdate(valid_till) }}{% endif %}.{% elif rejection_reason %}Reason: "
				"{{ rejection_reason }}{% endif %}",
				action="Go to certificates",
			),
		},
		ev.IN_APP: {
			"subject": "Bonafide certificate {{ status|lower }}",
			"body": "{{ request }}{% if certificate_number %}: certificate {{ certificate_number }} is "
			"ready to download.{% elif rejection_reason %}: {{ rejection_reason }}{% else %}.{% endif %}",
		},
	},
	ev.SUPPORT_TICKET_UPDATE: {
		ev.SMS: {
			"body": "HyFib support ticket {{ ticket }} is now {{ status }}: {{ short(subject) }}. "
			"-HyFib Support",
			"template_parameters": "ticket\nstatus\nshort(subject)",
		},
		ev.WHATSAPP: {
			"body": "HyFib support ticket {{ ticket }} ({{ subject }}) is now {{ status }}. "
			"Update: {{ %s or 'See the ticket in Hostel360' }}" % TICKET_UPDATE,
			"template_parameters": f"ticket\nsubject\nstatus\n{TICKET_UPDATE} or 'See the ticket in Hostel360'",
		},
		ev.EMAIL: {
			"subject": "[{{ ticket }}] {{ subject }}: {{ status }}",
			"body": _email(
				"Support ticket {{ ticket }} has been updated.",
				_rows(
					("Subject", "{{ subject }}"),
					("Status", "{{ status }}{% if previous_status %} (was {{ previous_status }}){% endif %}"),
				),
				"{%% if %s %%}%s{%% endif %%}" % (TICKET_UPDATE, _quote(TICKET_UPDATE)),
				"Reply on the ticket in Hostel360 or call HyFib support on {{ support_phone }}.",
				action="Open the ticket",
				greeting="Colleague",
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "Ticket {{ ticket }}: {{ status }}",
			"body": "{{ subject }}{%% if %s %%}: {{ %s }}{%% endif %%}" % (TICKET_UPDATE, TICKET_UPDATE),
		},
	},
	ev.MESS_LOW_STOCK: {
		ev.SMS: {
			"body": "Low stock at {{ short(mess, 20) }}: {{ item_count }} items below reorder level, "
			"including {{ short(items_summary) }}. Please raise an indent." + SIGN_OFF,
			"template_parameters": "short(mess, 20)\nitem_count\nshort(items_summary)",
		},
		ev.WHATSAPP: {
			"body": "Low stock at {{ mess }}: {{ item_count }} items are below their reorder level, "
			"including {{ items_summary }}. Please raise an indent in Hostel360.",
			"template_parameters": "mess\nitem_count\nitems_summary",
		},
		ev.EMAIL: {
			"subject": "Low stock at {{ mess }}: {{ item_count }} items below reorder level",
			"body": _email(
				"The following items at {{ mess }} are at or below their reorder level.",
				'<table style="border-collapse:collapse;margin:12px 0">'
				'<tr><th style="text-align:left;padding:6px 12px 6px 0">Item</th>'
				'<th style="text-align:right;padding:6px 12px">In stock</th>'
				'<th style="text-align:right;padding:6px 0">Reorder level</th></tr>'
				"{% for row in items %}<tr>"
				'<td style="padding:6px 12px 6px 0">{{ row.item }}</td>'
				'<td style="text-align:right;padding:6px 12px">{{ row.qty }} {{ row.uom }}</td>'
				'<td style="text-align:right;padding:6px 0">{{ row.reorder_level }} {{ row.uom }}</td>'
				'</tr>{% else %}<tr><td colspan="3">{{ items_summary }}</td></tr>{% endfor %}</table>',
				"Please raise a mess indent so the items can be ordered in time.",
				action="Open Hostel360",
				greeting="Mess Manager",
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "Low stock: {{ item_count }} items at {{ mess }}",
			"body": "{{ items_summary }}",
		},
	},
}


def _with_defaults(event: str, channel: str, spec: dict) -> dict:
	row = {"event": event, "channel": channel, "enabled": 1, **spec}
	if channel == ev.WHATSAPP:
		row.setdefault("whatsapp_template_name", _wa(event))
		row.setdefault("whatsapp_language", "en")
	return row


def get_default_templates() -> list[dict]:
	"""Flat list of template definitions, one per event and channel."""
	return [
		_with_defaults(event, channel, spec)
		for event in ev.EVENTS
		for channel, spec in TEMPLATES.get(event, {}).items()
	]


def get_default_template(event: str, channel: str) -> dict | None:
	spec = TEMPLATES.get(event, {}).get(channel)
	return _with_defaults(event, channel, spec) if spec else None
