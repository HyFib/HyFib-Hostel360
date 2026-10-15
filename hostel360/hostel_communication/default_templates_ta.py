"""Tamil (ta) wording for every messaging event.

The variables and aliases are the same as in the English templates (``default_templates``).
Fixed values that callers pass in English (statuses, categories, payment modes, purposes)
are shown with ``label(...)``, so they read in Tamil, and dates and times are formatted in
the Tamil locale. Terms follow the student and parent portal: காப்பாளர் (warden), விடுப்பு
(leave), உணவகக் கட்டணக் கழிவு (mess reduction), கட்டணச்சீட்டு (invoice), புகார்
(grievance).

Tamil SMS are Unicode messages, so they run to several 70-character parts; they contain no
Jinja conditionals, because DLT scrubbing compares each SMS with the registered Unicode
template. WhatsApp bodies are the Tamil (``ta``) versions of the same WhatsApp Manager
templates, with ``template_parameters`` in the order the values appear in the Tamil text.
"""

from hostel360.hostel_communication import events as ev
from hostel360.hostel_communication.default_templates import (
	BED,
	GRIEVANCE_MESSAGE,
	LEAVE_REMARKS,
	OUTSTANDING,
	ROOM,
	TICKET_UPDATE,
	_quote,
	_rows,
)
from hostel360.hostel_communication.default_templates import _email as _english_email

SIGN_OFF = " -{{ institution_short_name }} விடுதி"

STUDENT = "மாணவர்"
PARENT = "பெற்றோர்"
WARDEN = "காப்பாளர்"
COLLEAGUE = "சக பணியாளர்"
MESS_MANAGER = "உணவக மேலாளர்"

APPROVALS_PAGE = (
	"Hostel360 இன் ஒப்புதல்கள் (Approvals) பக்கத்திலிருந்து ஒப்புதல் அளிக்கலாம் அல்லது நிராகரிக்கலாம்."
)
INVOICE_TYPE = "label(invoice_type) if invoice_type else 'விடுதிக் கட்டணம்'"


def _email(*paragraphs, action: str | None = None, greeting: str = STUDENT, staff: bool = False) -> str:
	return _english_email(*paragraphs, action=action, greeting=greeting, staff=staff, language=ev.TAMIL)


TEMPLATES = {
	ev.OTP_LOGIN: {
		ev.SMS: {
			"body": "{{ otp }} - {{ institution_short_name }} விடுதி இணையதளத்தில் உள்நுழைவதற்கான உங்கள் "
			"OTP. இது {{ expiry_minutes }} நிமிடங்கள் மட்டுமே செல்லுபடியாகும். யாரிடமும் பகிர வேண்டாம்."
			"{% if portal_domain %}\n\n@{{ portal_domain }} #{{ otp }}{% endif %}",
			"template_parameters": "otp\nexpiry_minutes",
		},
		ev.WHATSAPP: {
			"body": "{{ otp }} என்பது {{ institution_short_name }} விடுதி இணையதளத்திற்கான உங்கள் "
			"சரிபார்ப்புக் குறியீடு. உங்கள் பாதுகாப்பிற்காக இந்தக் குறியீட்டை யாரிடமும் பகிர வேண்டாம்.",
			"template_parameters": "otp",
		},
	},
	ev.STUDENT_VALIDATED: {
		ev.SMS: {
			"body": "அன்புள்ள {{ short(student_name) }}, உங்கள் விடுதிப் பதிவு {{ register_number }} "
			"சரிபார்க்கப்பட்டது. அனைத்துச் சேவைகளும் {{ portal_url }} இல் கிடைக்கும்." + SIGN_OFF,
			"template_parameters": "short(student_name)\nregister_number",
		},
		ev.WHATSAPP: {
			"body": "அன்புள்ள {{ student_name }}, உங்கள் விடுதிப் பதிவு {{ register_number }} விடுதி "
			"அலுவலகத்தால் சரிபார்க்கப்பட்டது. இனி விடுதி இணையதளத்தின் அனைத்துச் சேவைகளையும் "
			"பயன்படுத்தலாம்: {{ portal_url }}",
			"template_parameters": "student_name\nregister_number\nportal_url",
		},
		ev.EMAIL: {
			"subject": "உங்கள் விடுதிப் பதிவு சரிபார்க்கப்பட்டது",
			"body": _email(
				"உங்கள் விடுதிப் பதிவு (பதிவு எண் <strong>{{ register_number }}</strong>) விடுதி "
				"அலுவலகத்தால் சரிபார்க்கப்பட்டது.",
				"இனி விடுதி இணையதளத்தின் அனைத்துச் சேவைகளையும் பயன்படுத்தலாம்: இணைய வழியில் கட்டணம் "
				"செலுத்துதல், உணவுப் பட்டியலைப் பார்த்தல், விடுப்பு அல்லது உணவகக் கட்டணக் கழிவுக்கு "
				"விண்ணப்பித்தல், புகார் அளித்தல், சான்றிதழ் கோருதல்.",
				action="விடுதி இணையதளத்தைத் திற",
			),
		},
		ev.IN_APP: {
			"subject": "உங்கள் பதிவு சரிபார்க்கப்பட்டது",
			"body": "பதிவு எண் {{ register_number }} சரிபார்க்கப்பட்டது. இணையதளத்தின் அனைத்துச் சேவைகளும் "
			"இப்போது உங்களுக்குக் கிடைக்கும்.",
		},
	},
	ev.ROOM_ALLOTTED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} அவர்களுக்கு அறை ஒதுக்கப்பட்டது: {{ short(hostel_name) }}, "
			"அறை {{ %s }}, படுக்கை {{ %s }}, {{ fdate(from_date) }} முதல். விவரம்: {{ portal_url }}"
			% (ROOM, BED)
			+ SIGN_OFF,
			"template_parameters": f"short(student_name)\nshort(hostel_name)\n{ROOM}\n{BED}\nfdate(from_date)",
		},
		ev.WHATSAPP: {
			"body": "{{ student_name }} அவர்களுக்கு அறை ஒதுக்கப்பட்டது: {{ hostel_name }}, அறை {{ %s }}, "
			"படுக்கை {{ %s }}, {{ fdate(from_date) }} முதல். அறைச் சாவியைப் பெற உங்கள் அடையாள "
			"அட்டையுடன் விடுதி அலுவலகத்திற்கு வரவும்." % (ROOM, BED),
			"template_parameters": f"student_name\nhostel_name\n{ROOM}\n{BED}\nfdate(from_date)",
		},
		ev.EMAIL: {
			"subject": "அறை ஒதுக்கீடு: {{ hostel_name }}, அறை {{ %s }}" % ROOM,
			"body": _email(
				"{{ student_name }} அவர்களுக்கு விடுதி அறை ஒதுக்கப்பட்டுள்ளது.",
				_rows(
					("விடுதி", "{{ hostel_name }}"),
					("அறை", "{{ %s }}" % ROOM),
					("படுக்கை", "{{ %s }}" % BED),
					("தொடக்கத் தேதி", "{{ fdate(from_date) }}"),
				),
				"அறைச் சாவியைப் பெற உங்கள் கல்லூரி அடையாள அட்டையுடன் விடுதி அலுவலகத்திற்கு வரவும்.",
				action="அறை விவரங்களைப் பார்",
			),
		},
		ev.IN_APP: {
			"subject": "அறை {{ %s }} ஒதுக்கப்பட்டது" % ROOM,
			"body": "{{ hostel_name }}, அறை {{ %s }}, படுக்கை {{ %s }}, {{ fdate(from_date) }} முதல்."
			% (ROOM, BED),
		},
	},
	ev.FEE_DUE_PUBLISHED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} அவர்களின் விடுதிக் கட்டணம் {{ rupees(amount) }}, "
			"{{ fdate(due_date) }} க்குள் செலுத்த வேண்டும் (கட்டணச்சீட்டு {{ invoice }}). செலுத்த: "
			"{{ portal_url }}" + SIGN_OFF,
			"template_parameters": "short(student_name)\nrupees(amount)\nfdate(due_date)\ninvoice",
		},
		ev.WHATSAPP: {
			"body": "அன்புள்ள {{ recipient_name }}, {{ student_name }} அவர்களின் விடுதிக் கட்டணம் "
			"{{ rupees(amount) }}, {{ fdate(due_date) }} க்குள் செலுத்த வேண்டும் (கட்டணச்சீட்டு "
			"{{ invoice }}). UPI, கார்டு அல்லது நெட் பேங்கிங் மூலம் விடுதி இணையதளத்தில் பாதுகாப்பாகச் "
			"செலுத்தலாம்: {{ portal_url }}",
			"template_parameters": "recipient_name\nstudent_name\nrupees(amount)\nfdate(due_date)\ninvoice\n"
			"portal_url",
		},
		ev.EMAIL: {
			"subject": "விடுதிக் கட்டணம்: {{ format_inr(amount) }}, கெடு {{ fdate(due_date) }}",
			"body": _email(
				"{{ student_name }} அவர்களுக்கு விடுதிக் கட்டணம் வெளியிடப்பட்டுள்ளது.",
				_rows(
					("விவரம்", "{{ %s }}" % INVOICE_TYPE),
					("கட்டணச்சீட்டு", "{{ invoice }}"),
					("தொகை", "{{ format_inr(amount) }}"),
					("கெடு தேதி", "{{ fdate(due_date) }}"),
				),
				"UPI, டெபிட் அல்லது கிரெடிட் கார்டு, நெட் பேங்கிங் மூலம் இணைய வழியில் செலுத்தலாம். "
				"பணம் உறுதியானதும் ரசீது இணையதளத்தில் கிடைக்கும்.",
				action="இப்போது செலுத்து",
			),
		},
		ev.IN_APP: {
			"subject": "செலுத்த வேண்டிய கட்டணம்: {{ format_inr(amount) }}, கெடு {{ fdate(due_date) }}",
			"body": "{{ student_name }} அவர்களின் {{ %s }} (கட்டணச்சீட்டு {{ invoice }}). கட்டணம் "
			"பக்கத்திலிருந்து இணைய வழியில் செலுத்தலாம்." % INVOICE_TYPE,
		},
	},
	ev.FEE_REMINDER: {
		ev.SMS: {
			"body": "நினைவூட்டல்: {{ short(student_name) }} அவர்களின் விடுதிக் கட்டணம் {{ rupees(%s) }} "
			"(கெடு {{ fdate(due_date) }}) இன்னும் செலுத்தப்படவில்லை. செலுத்த: {{ portal_url }}"
			% OUTSTANDING
			+ SIGN_OFF,
			"template_parameters": f"short(student_name)\nrupees({OUTSTANDING})\nfdate(due_date)",
		},
		ev.WHATSAPP: {
			"body": "அன்புள்ள {{ recipient_name }}, {{ student_name }} அவர்களின் கட்டணச்சீட்டு {{ invoice }} "
			"இல் {{ rupees(%s) }} நிலுவையில் உள்ளது; கெடு {{ fdate(due_date) }}. விடுதி இணையதளத்தில் "
			"செலுத்தவும்: {{ portal_url }}. ஏற்கனவே செலுத்தியிருந்தால் இந்தச் செய்தியைப் பொருட்படுத்த "
			"வேண்டாம்." % OUTSTANDING,
			"template_parameters": f"recipient_name\nstudent_name\ninvoice\nrupees({OUTSTANDING})\n"
			"fdate(due_date)\nportal_url",
		},
		ev.EMAIL: {
			"subject": "{% if is_overdue %}கெடு தாண்டியது{% else %}நினைவூட்டல்{% endif %}: "
			"{{ student_name }} அவர்களின் விடுதிக் கட்டணம் {{ format_inr(" + OUTSTANDING + ") }}",
			"body": _email(
				"{% if is_overdue %}கீழே உள்ள விடுதிக் கட்டணத்தின் கெடு {{ fdate(due_date) }}; அது இன்னும் "
				"செலுத்தப்படவில்லை.{% else %}கீழே உள்ள விடுதிக் கட்டணம் {{ fdate(due_date) }} அன்று "
				"செலுத்த வேண்டும் என்பதை அன்புடன் நினைவூட்டுகிறோம்.{% endif %}",
				_rows(
					("மாணவர்", "{{ student_name }}"),
					("கட்டணச்சீட்டு", "{{ invoice }}"),
					("நிலுவைத் தொகை", "{{ format_inr(%s) }}" % OUTSTANDING),
					("கெடு தேதி", "{{ fdate(due_date) }}"),
				),
				"ஏற்கனவே செலுத்தியிருந்தால் இந்தச் செய்தியைப் பொருட்படுத்த வேண்டாம். சில நிமிடங்களுக்கு முன் "
				"செலுத்திய தொகை இங்கே தெரிய சிறிது நேரம் ஆகலாம்.",
				action="இப்போது செலுத்து",
			),
		},
		ev.IN_APP: {
			"subject": "{% if is_overdue %}கெடு தாண்டியது{% else %}விரைவில் கெடு{% endif %}: "
			"{{ format_inr(" + OUTSTANDING + ") }}",
			"body": "{{ student_name }} அவர்களின் கட்டணச்சீட்டு {{ invoice }}, கெடு {{ fdate(due_date) }}.",
		},
	},
	ev.PAYMENT_RECEIVED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} அவர்களுக்காக {{ rupees(amount) }} {{ fdate(payment_date) }} "
			"அன்று பெறப்பட்டது. ரசீது எண் {{ receipt_number }}. நன்றி." + SIGN_OFF,
			"template_parameters": "short(student_name)\nrupees(amount)\nfdate(payment_date)\nreceipt_number",
		},
		ev.WHATSAPP: {
			"body": "நன்றி. {{ student_name }} அவர்களுக்காக {{ rupees(amount) }} {{ fdate(payment_date) }} "
			"அன்று {{ label(mode_of_payment) }} மூலம் பெறப்பட்டது. ரசீது எண் {{ receipt_number }}. "
			"ரசீதை விடுதி இணையதளத்திலிருந்து பதிவிறக்கலாம்.",
			"template_parameters": "student_name\nrupees(amount)\nfdate(payment_date)\nlabel(mode_of_payment)\n"
			"receipt_number",
		},
		ev.EMAIL: {
			"subject": "பணம் பெறப்பட்டது: {{ format_inr(amount) }} (ரசீது {{ receipt_number }})",
			"body": _email(
				"நன்றி. {{ student_name }} அவர்களுக்காகக் கீழ்க்கண்ட தொகை பெறப்பட்டது.",
				_rows(
					("ரசீது எண்", "{{ receipt_number }}"),
					("தொகை", "{{ format_inr(amount) }}"),
					("தேதி", "{{ fdate(payment_date) }}"),
					("செலுத்திய முறை", "{{ label(mode_of_payment) }}"),
				),
				"விடுதி இணையதளத்தின் ரசீதுகள் பக்கத்திலிருந்து ரசீதை எப்போது வேண்டுமானாலும் பதிவிறக்கலாம்.",
				action="ரசீதைப் பதிவிறக்கு",
			),
		},
		ev.IN_APP: {
			"subject": "பணம் பெறப்பட்டது: {{ format_inr(amount) }}",
			"body": "ரசீது {{ receipt_number }}, தேதி {{ fdate(payment_date) }} ({{ label(mode_of_payment) }}).",
		},
	},
	ev.MESS_BILL_PUBLISHED: {
		ev.SMS: {
			"body": "{{ period }} உணவகக் கட்டணம்: {{ short(student_name) }} அவர்களுக்கு {{ rupees(amount) }} "
			"({{ chargeable_days }} நாட்கள்), கெடு {{ fdate(due_date) }}. செலுத்த: {{ portal_url }}"
			+ SIGN_OFF,
			"template_parameters": "period\nshort(student_name)\nrupees(amount)\nchargeable_days\nfdate(due_date)",
		},
		ev.WHATSAPP: {
			"body": "அன்புள்ள {{ recipient_name }}, {{ period }} மாதத்திற்கான {{ student_name }} அவர்களின் "
			"உணவகக் கட்டணம் {{ rupees(amount) }} ({{ chargeable_days }} நாட்கள், ஒரு நாளுக்கு "
			"{{ rupees(daily_rate) }}). {{ fdate(due_date) }} க்குள் விடுதி இணையதளத்தில் செலுத்தவும்: "
			"{{ portal_url }}",
			"template_parameters": "recipient_name\nperiod\nstudent_name\nrupees(amount)\nchargeable_days\n"
			"rupees(daily_rate)\nfdate(due_date)\nportal_url",
		},
		ev.EMAIL: {
			"subject": "{{ period }} உணவகக் கட்டணம்: {{ format_inr(amount) }}",
			"body": _email(
				"{{ student_name }} அவர்களுக்கு {{ period }} மாதத்திற்கான உணவகக் கட்டணம் "
				"வெளியிடப்பட்டுள்ளது.",
				_rows(
					("கட்டண நாட்கள்", "{{ chargeable_days }}"),
					("ஒரு நாள் கட்டணம்", "{{ format_inr(daily_rate) }}"),
					("தொகை", "{{ format_inr(amount) }}"),
					("கட்டணச்சீட்டு", "{{ invoice }}"),
					("கெடு தேதி", "{{ fdate(due_date) }}"),
				),
				"ஒப்புதல் பெற்ற உணவகக் கட்டணக் கழிவு நாட்களுக்குக் கட்டணம் வசூலிக்கப்படாது. விரிவான "
				"கட்டண விவரம் உணவகக் கட்டணங்கள் பக்கத்தில் உள்ளது.",
				action="பார்த்துச் செலுத்து",
			),
		},
		ev.IN_APP: {
			"subject": "{{ period }} உணவகக் கட்டணம்: {{ format_inr(amount) }}",
			"body": "{{ chargeable_days }} நாட்கள், ஒரு நாளுக்கு {{ format_inr(daily_rate) }}. கெடு "
			"{{ fdate(due_date) }}.",
		},
	},
	ev.MENU_PUBLISHED: {
		ev.SMS: {
			"body": "{{ short(mess) }} உணவுப் பட்டியல் ({{ fdate(week_start) }} முதல் {{ fdate(week_end) }} "
			"வரை) வெளியிடப்பட்டது: {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "short(mess)\nfdate(week_start)\nfdate(week_end)",
		},
		ev.WHATSAPP: {
			"body": "{{ fdate(week_start) }} முதல் {{ fdate(week_end) }} வரையிலான வாரத்திற்கான {{ mess }} "
			"உணவுப் பட்டியல் வெளியிடப்பட்டுள்ளது. விடுதி இணையதளத்தில் பார்க்கவும்: {{ portal_url }}",
			"template_parameters": "fdate(week_start)\nfdate(week_end)\nmess\nportal_url",
		},
		ev.EMAIL: {
			"subject": "உணவுப் பட்டியல்: {{ fdate(week_start) }} முதல் {{ fdate(week_end) }} வரை",
			"body": _email(
				"{{ fdate(week_start) }} முதல் {{ fdate(week_end) }} வரையிலான வாரத்திற்கான {{ mess }} "
				"உணவுப் பட்டியல் வெளியிடப்பட்டுள்ளது.",
				action="இந்த வார உணவுப் பட்டியல்",
			),
		},
		ev.IN_APP: {
			"subject": "இந்த வார உணவுப் பட்டியல் வெளியானது",
			"body": "{{ mess }}: {{ fdate(week_start) }} முதல் {{ fdate(week_end) }} வரை.",
		},
	},
	ev.MESS_REDUCTION_SUBMITTED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} அவர்களின் உணவகக் கட்டணக் கழிவு விண்ணப்பம் {{ application }} "
			"({{ fdate(from_date) }} முதல் {{ fdate(to_date) }} வரை, {{ number_of_days }} நாட்கள்) "
			"ஒப்புதலுக்குச் சமர்ப்பிக்கப்பட்டது." + SIGN_OFF,
			"template_parameters": "short(student_name)\napplication\nfdate(from_date)\nfdate(to_date)\n"
			"number_of_days",
		},
		ev.WHATSAPP: {
			"body": "{{ student_name }} அவர்களின் உணவகக் கட்டணக் கழிவு விண்ணப்பம் {{ application }} "
			"({{ fdate(from_date) }} முதல் {{ fdate(to_date) }} வரை, {{ number_of_days }} நாட்கள், "
			"காரணம்: {{ label(reason) }}) ஒப்புதலுக்குச் சமர்ப்பிக்கப்பட்டுள்ளது.",
			"template_parameters": "student_name\napplication\nfdate(from_date)\nfdate(to_date)\n"
			"number_of_days\nlabel(reason)",
		},
		ev.EMAIL: {
			"subject": "உணவகக் கட்டணக் கழிவு விண்ணப்பம் {{ application }} சமர்ப்பிக்கப்பட்டது",
			"body": _email(
				"{{ student_name }} அவர்களின் உணவகக் கட்டணக் கழிவு விண்ணப்பம் உங்கள் ஒப்புதலுக்காகக் "
				"காத்திருக்கிறது.",
				_rows(
					("விண்ணப்பம்", "{{ application }}"),
					("தொடக்கத் தேதி", "{{ fdate(from_date) }}"),
					("முடிவுத் தேதி", "{{ fdate(to_date) }}"),
					("நாட்கள்", "{{ number_of_days }}"),
					("காரணம்", "{{ label(reason) }}"),
				),
				APPROVALS_PAGE,
				action="விண்ணப்பத்தைப் பார்",
				greeting=COLLEAGUE,
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "பரிசீலிக்க வேண்டிய உணவகக் கட்டணக் கழிவு: {{ student_name }}",
			"body": "{{ application }}: {{ fdate(from_date) }} முதல் {{ fdate(to_date) }} வரை, "
			"{{ number_of_days }} நாட்கள் ({{ label(reason) }}).",
		},
	},
	ev.MESS_REDUCTION_DECIDED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} அவர்களின் உணவகக் கட்டணக் கழிவு {{ application }} "
			"({{ fdate(from_date) }} முதல் {{ fdate(to_date) }} வரை): {{ label(status) }}." + SIGN_OFF,
			"template_parameters": "short(student_name)\napplication\nfdate(from_date)\nfdate(to_date)\n"
			"label(status)",
		},
		ev.WHATSAPP: {
			"body": "{{ student_name }} அவர்களின் உணவகக் கட்டணக் கழிவு விண்ணப்பம் {{ application }} "
			"({{ fdate(from_date) }} முதல் {{ fdate(to_date) }} வரை) - முடிவு: {{ label(status) }}. "
			"குறிப்பு: {{ remarks or 'இல்லை' }}",
			"template_parameters": "student_name\napplication\nfdate(from_date)\nfdate(to_date)\nlabel(status)\n"
			"remarks or 'இல்லை'",
		},
		ev.EMAIL: {
			"subject": "உணவகக் கட்டணக் கழிவு {{ application }}: {{ label(status) }}",
			"body": _email(
				"{{ student_name }} அவர்களின் உணவகக் கட்டணக் கழிவு விண்ணப்பம் {{ application }} "
				"({{ fdate(from_date) }} முதல் {{ fdate(to_date) }} வரை, {{ number_of_days }} நாட்கள்) "
				"குறித்த முடிவு: <strong>{{ label(status) }}</strong>.",
				"{% if remarks %}குறிப்பு: {{ remarks }}{% else %}குறிப்பு எதுவும் சேர்க்கப்படவில்லை.{% endif %}",
				"{% if status == 'Approved' %}இந்த நாட்களுக்கு உணவகக் கட்டணம் வசூலிக்கப்படாது.{% else %}"
				"ஏதேனும் சந்தேகம் இருந்தால் உணவக அலுவலகத்தைத் தொடர்பு கொள்ளவும்.{% endif %}",
				action="விவரங்களைப் பார்",
			),
		},
		ev.IN_APP: {
			"subject": "உணவகக் கட்டணக் கழிவு: {{ label(status) }}{% if student_name %} - {{ student_name }}"
			"{% endif %}",
			"body": "{{ application }}: {{ fdate(from_date) }} முதல் {{ fdate(to_date) }} வரை."
			"{% if remarks %} குறிப்பு: {{ remarks }}{% endif %}",
		},
	},
	ev.LEAVE_SUBMITTED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} அவர்களின் விடுப்புக் கோரிக்கை {{ leave }} "
			"({{ fdatetime(from_datetime) }} முதல் {{ fdatetime(to_datetime) }} வரை) சமர்ப்பிக்கப்பட்டது. "
			"நிலை: {{ label(status) }}." + SIGN_OFF,
			"template_parameters": "short(student_name)\nleave\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)\nlabel(status)",
		},
		ev.WHATSAPP: {
			"body": "{{ student_name }} அவர்களின் விடுப்புக் கோரிக்கை {{ leave }} ({{ destination }}, "
			"{{ fdatetime(from_datetime) }} முதல் {{ fdatetime(to_datetime) }} வரை) "
			"சமர்ப்பிக்கப்பட்டுள்ளது. தற்போதைய நிலை: {{ label(status) }}.",
			"template_parameters": "student_name\nleave\ndestination\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)\nlabel(status)",
		},
		ev.EMAIL: {
			"subject": "விடுப்புக் கோரிக்கை {{ leave }} சமர்ப்பிக்கப்பட்டது",
			"body": _email(
				"{{ student_name }} அவர்களுக்கு வீட்டுக்குச் செல்லும் விடுப்புக் கோரிக்கை "
				"சமர்ப்பிக்கப்பட்டுள்ளது.",
				_rows(
					("கோரிக்கை", "{{ leave }}"),
					("செல்லும் இடம்", "{{ destination }}"),
					("புறப்படும் நேரம்", "{{ fdatetime(from_datetime) }}"),
					("திரும்பும் நேரம்", "{{ fdatetime(to_datetime) }}"),
					("நிலை", "{{ label(status) }}"),
				),
				"ஒப்புதலின் ஒவ்வொரு கட்டத்திலும் உங்களுக்குத் தெரிவிக்கப்படும்.",
				action="கோரிக்கையைப் பின்தொடர்",
			),
		},
		ev.IN_APP: {
			"subject": "விடுப்புக் கோரிக்கை சமர்ப்பிக்கப்பட்டது",
			"body": "{{ leave }}: {{ fdatetime(from_datetime) }} முதல் {{ fdatetime(to_datetime) }} வரை. "
			"நிலை: {{ label(status) }}.",
		},
	},
	ev.LEAVE_PARENT_APPROVAL_REQUIRED: {
		ev.SMS: {
			"body": "{{ short(student_name) }}, {{ short(destination, 20) }} செல்ல "
			"{{ fdatetime(from_datetime) }} முதல் {{ fdatetime(to_datetime) }} வரை விடுப்பு கோரியுள்ளார். "
			"பதிலளிக்க: {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "short(student_name)\nshort(destination, 20)\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)",
		},
		ev.WHATSAPP: {
			"body": "அன்புள்ள {{ recipient_name }}, {{ student_name }}, {{ destination }} செல்ல "
			"{{ fdatetime(from_datetime) }} முதல் {{ fdatetime(to_datetime) }} வரை விடுப்பு கோரியுள்ளார். "
			"காரணம்: {{ reason }}. விடுதி இணையதளத்தில் ஒப்புதல் அளிக்கவும் அல்லது நிராகரிக்கவும்: "
			"{{ portal_url }}",
			"template_parameters": "recipient_name\nstudent_name\ndestination\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)\nreason\nportal_url",
		},
		ev.EMAIL: {
			"subject": "{{ student_name }} அவர்களின் விடுப்புக் கோரிக்கையைப் பரிசீலிக்கவும்",
			"body": _email(
				"{{ student_name }} வீட்டுக்குச் செல்ல விடுப்பு கோரியுள்ளார். காப்பாளர் பரிசீலிக்கும் முன் "
				"உங்கள் ஒப்புதல் தேவை.",
				_rows(
					("செல்லும் இடம்", "{{ destination }}"),
					("புறப்படும் நேரம்", "{{ fdatetime(from_datetime) }}"),
					("திரும்பும் நேரம்", "{{ fdatetime(to_datetime) }}"),
					("காரணம்", "{{ reason }}"),
				),
				"இந்தக் கோரிக்கையை நீங்கள் எதிர்பார்க்கவில்லை என்றால், விடுதி அலுவலகத்தை அழைக்கவும்.",
				action="ஒப்புதல் அளி அல்லது நிராகரி",
				greeting=PARENT,
			),
		},
		ev.IN_APP: {
			"subject": "உங்கள் ஒப்புதலுக்காகக் காத்திருக்கும் விடுப்புக் கோரிக்கை",
			"body": "{{ student_name }}: {{ destination }}, {{ fdatetime(from_datetime) }} முதல் "
			"{{ fdatetime(to_datetime) }} வரை.",
		},
	},
	ev.LEAVE_WARDEN_APPROVAL_REQUIRED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} ({{ hostel }}) அவர்களின் விடுப்பு {{ leave }}, "
			"{{ fdatetime(from_datetime) }} முதல் {{ fdatetime(to_datetime) }} வரை, உங்கள் ஒப்புதலுக்காகக் "
			"காத்திருக்கிறது." + SIGN_OFF,
			"template_parameters": "short(student_name)\nhostel\nleave\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)",
		},
		ev.WHATSAPP: {
			"body": "{{ student_name }} ({{ hostel }}) அவர்களின் விடுப்புக் கோரிக்கை {{ leave }} "
			"({{ destination }}, {{ fdatetime(from_datetime) }} முதல் {{ fdatetime(to_datetime) }} வரை) "
			"உங்கள் ஒப்புதலுக்காகக் காத்திருக்கிறது.",
			"template_parameters": "student_name\nhostel\nleave\ndestination\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)",
		},
		ev.EMAIL: {
			"subject": "விடுப்பு ஒப்புதல் தேவை: {{ student_name }} ({{ leave }})",
			"body": _email(
				"கீழ்க்கண்ட விடுப்புக் கோரிக்கை உங்கள் ஒப்புதலுக்காகக் காத்திருக்கிறது.",
				_rows(
					("மாணவர்", "{{ student_name }}"),
					("விடுதி", "{{ hostel }}"),
					("செல்லும் இடம்", "{{ destination }}"),
					("புறப்படும் நேரம்", "{{ fdatetime(from_datetime) }}"),
					("திரும்பும் நேரம்", "{{ fdatetime(to_datetime) }}"),
					("காரணம்", "{{ reason }}"),
				),
				APPROVALS_PAGE,
				action="கோரிக்கையைப் பார்",
				greeting=WARDEN,
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "விடுப்பு ஒப்புதல் தேவை: {{ student_name }}",
			"body": "{{ leave }} ({{ hostel }}): {{ fdatetime(from_datetime) }} முதல் "
			"{{ fdatetime(to_datetime) }} வரை, {{ destination }}.",
		},
	},
	ev.LEAVE_DECIDED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} அவர்களின் விடுப்பு {{ leave }} ({{ fdatetime(from_datetime) }} "
			"முதல் {{ fdatetime(to_datetime) }} வரை): {{ label(status) }}." + SIGN_OFF,
			"template_parameters": "short(student_name)\nleave\nfdatetime(from_datetime)\n"
			"fdatetime(to_datetime)\nlabel(status)",
		},
		ev.WHATSAPP: {
			"body": "{{ student_name }} அவர்களின் விடுப்புக் கோரிக்கை {{ leave }} "
			"({{ fdatetime(from_datetime) }} முதல் {{ fdatetime(to_datetime) }} வரை) - முடிவு: "
			"{{ label(status) }}. குறிப்பு: {{ %s or 'இல்லை' }}" % LEAVE_REMARKS,
			"template_parameters": "student_name\nleave\nfdatetime(from_datetime)\nfdatetime(to_datetime)\n"
			f"label(status)\n{LEAVE_REMARKS} or 'இல்லை'",
		},
		ev.EMAIL: {
			"subject": "விடுப்புக் கோரிக்கை {{ leave }}: {{ label(status) }}",
			"body": _email(
				"{{ student_name }} அவர்களின் விடுப்புக் கோரிக்கை {{ leave }} ({{ fdatetime(from_datetime) }} "
				"முதல் {{ fdatetime(to_datetime) }} வரை) குறித்த முடிவு: <strong>{{ label(status) }}</strong>.",
				"{%% if %s %%}குறிப்பு: {{ %s }}{%% else %%}குறிப்பு எதுவும் சேர்க்கப்படவில்லை.{%% endif %%}"
				% (LEAVE_REMARKS, LEAVE_REMARKS),
				"{% if status == 'Approved' %}புறப்படும்போதும் திரும்பும்போதும் விடுதி வாயிலில் பதிவு "
				"செய்யவும்.{% endif %}",
				action="கோரிக்கையைப் பார்",
			),
		},
		ev.IN_APP: {
			"subject": "விடுப்பு: {{ label(status) }}{% if student_name %} - {{ student_name }}{% endif %}",
			"body": "{{ leave }}: {{ fdatetime(from_datetime) }} முதல் {{ fdatetime(to_datetime) }} வரை."
			"{%% if %s %%} குறிப்பு: {{ %s }}{%% endif %%}" % (LEAVE_REMARKS, LEAVE_REMARKS),
		},
	},
	ev.LEAVE_OVERDUE: {
		ev.SMS: {
			"body": "{{ fdatetime(to_datetime) }} க்குள் திரும்ப வேண்டிய {{ short(student_name) }} "
			"({{ hostel }}) இன்னும் விடுதிக்குத் திரும்பவில்லை. விடுதி அலுவலகத்தைத் தொடர்பு கொள்ளவும்."
			+ SIGN_OFF,
			"template_parameters": "fdatetime(to_datetime)\nshort(student_name)\nhostel",
		},
		ev.WHATSAPP: {
			"body": "விடுப்பு {{ leave }} முடிந்து {{ fdatetime(to_datetime) }} க்குள் திரும்ப வேண்டிய "
			"{{ student_name }} இன்னும் {{ hostel }} விடுதிக்குத் திரும்பவில்லை. தாமதமானால் "
			"{{ support_phone }} என்ற எண்ணில் தொடர்பு கொள்ளவும்.",
			"template_parameters": "leave\nfdatetime(to_datetime)\nstudent_name\nhostel\nsupport_phone",
		},
		ev.EMAIL: {
			"subject": "விடுப்பிலிருந்து திரும்புவதில் தாமதம்: {{ student_name }}",
			"body": _email(
				"{{ destination }} சென்ற விடுப்பு {{ leave }} முடிந்து {{ fdatetime(to_datetime) }} க்குள் "
				"திரும்ப வேண்டிய {{ student_name }} இன்னும் {{ hostel }} விடுதிக்குத் திரும்பவில்லை.",
				"நியாயமான காரணத்தால் தாமதம் ஏற்பட்டால், காப்பாளருக்குத் தெரியும்படி விடுதி அலுவலகத்திற்குத் "
				"தெரிவிக்கவும்.",
			),
		},
		ev.IN_APP: {
			"subject": "திரும்புவதில் தாமதம்: {{ student_name }}",
			"body": "விடுப்பு {{ leave }} ({{ hostel }}) முடிந்து {{ fdatetime(to_datetime) }} க்குள் "
			"திரும்பியிருக்க வேண்டும்.",
		},
	},
	ev.LEAVE_CHECKED_OUT: {
		ev.SMS: {
			"body": "{{ short(student_name) }} அனுமதி பெற்ற விடுப்பில் {{ fdatetime(checked_out_on) }} அன்று "
			"{{ short(hostel, 20) }} விடுதியிலிருந்து வெளியேறினார். திரும்ப வேண்டிய நேரம்: "
			"{{ fdatetime(to_datetime) }}." + SIGN_OFF,
			"template_parameters": "short(student_name)\nfdatetime(checked_out_on)\nshort(hostel, 20)\n"
			"fdatetime(to_datetime)",
		},
		ev.WHATSAPP: {
			"body": "அன்புள்ள {{ recipient_name }}, {{ student_name }} அனுமதி பெற்ற விடுப்பில் "
			"{{ destination }} செல்ல {{ fdatetime(checked_out_on) }} அன்று {{ hostel }} விடுதியிலிருந்து "
			"வெளியேறினார். திரும்ப வேண்டிய நேரம்: {{ fdatetime(to_datetime) }}.",
			"template_parameters": "recipient_name\nstudent_name\ndestination\nfdatetime(checked_out_on)\n"
			"hostel\nfdatetime(to_datetime)",
		},
		ev.EMAIL: {
			"subject": "{{ student_name }} விடுப்பில் விடுதியிலிருந்து வெளியேறினார்",
			"body": _email(
				"{{ student_name }} அனுமதி பெற்ற விடுப்பில் விடுதி வாயிலில் பதிவு செய்து வெளியேறினார்.",
				_rows(
					("விடுப்பு", "{{ leave }}"),
					("விடுதி", "{{ hostel }}"),
					("செல்லும் இடம்", "{{ destination }}"),
					("வெளியேறிய நேரம்", "{{ fdatetime(checked_out_on) }}"),
					("திரும்ப வேண்டிய நேரம்", "{{ fdatetime(to_datetime) }}"),
				),
				"இதை நீங்கள் எதிர்பார்க்கவில்லை என்றால், உடனே விடுதி அலுவலகத்தை அழைக்கவும்.",
				action="விடுப்பைப் பார்",
				greeting=PARENT,
			),
		},
		ev.IN_APP: {
			"subject": "{{ student_name }} விடுப்பில் வெளியேறினார்",
			"body": "{{ leave }}: {{ fdatetime(checked_out_on) }} அன்று {{ hostel }} விடுதியிலிருந்து "
			"வெளியேறினார்; {{ fdatetime(to_datetime) }} க்குள் திரும்ப வேண்டும்.",
		},
	},
	ev.LEAVE_RETURNED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} விடுப்பு முடிந்து {{ fdatetime(returned_on) }} அன்று "
			"{{ short(hostel, 20) }} விடுதிக்குத் திரும்பினார்." + SIGN_OFF,
			"template_parameters": "short(student_name)\nfdatetime(returned_on)\nshort(hostel, 20)",
		},
		ev.WHATSAPP: {
			"body": "அன்புள்ள {{ recipient_name }}, {{ student_name }} விடுப்பு {{ leave }} முடிந்து "
			"{{ fdatetime(returned_on) }} அன்று {{ hostel }} விடுதிக்குத் திரும்பினார்.",
			"template_parameters": "recipient_name\nstudent_name\nleave\nfdatetime(returned_on)\nhostel",
		},
		ev.EMAIL: {
			"subject": "{{ student_name }} விடுதிக்குத் திரும்பினார்",
			"body": _email(
				"{{ student_name }} விடுப்பு முடிந்து விடுதிக்குத் திரும்பி, வாயிலில் பதிவு செய்தார்.",
				_rows(
					("விடுப்பு", "{{ leave }}"),
					("விடுதி", "{{ hostel }}"),
					("வெளியேறிய நேரம்", "{{ fdatetime(checked_out_on) or '-' }}"),
					("திரும்பிய நேரம்", "{{ fdatetime(returned_on) }}"),
				),
				action="விடுப்பைப் பார்",
				greeting=PARENT,
			),
		},
		ev.IN_APP: {
			"subject": "{{ student_name }} விடுப்பிலிருந்து திரும்பினார்",
			"body": "{{ leave }}: {{ fdatetime(returned_on) }} அன்று {{ hostel }} விடுதிக்குத் திரும்பினார்.",
		},
	},
	ev.GRIEVANCE_RECEIVED: {
		ev.SMS: {
			"body": "புகார் {{ grievance }} ({{ short(subject, 30) }}) பெறப்பட்டது; விரைவில் கவனிக்கப்படும்."
			+ SIGN_OFF,
			"template_parameters": "grievance\nshort(subject, 30)",
		},
		ev.WHATSAPP: {
			"body": "புகார் {{ grievance }} பெறப்பட்டது. வகை: {{ label(category) }}. முன்னுரிமை: "
			"{{ label(priority) }}. தலைப்பு: {{ subject }}. நிலவரங்கள் விடுதி இணையதளத்தில் பகிரப்படும்.",
			"template_parameters": "grievance\nlabel(category)\nlabel(priority)\nsubject",
		},
		ev.EMAIL: {
			"subject": "புகார் {{ grievance }} பெறப்பட்டது: {{ subject }}",
			"body": _email(
				"புகார் {{ grievance }} பெறப்பட்டது.",
				_rows(
					("மாணவர்", "{{ student_name or 'ரகசியம்' }}"),
					("விடுதி", "{{ hostel }}"),
					("தலைப்பு", "{{ subject }}"),
					("வகை", "{{ label(category) }}"),
					("முன்னுரிமை", "{{ label(priority) }}"),
				),
				"விடுதி அலுவலகம் இதைக் கவனிக்கும்போது நிலவரங்கள் பகிரப்படும்.",
				action="புகாரைப் பார்",
			),
		},
		ev.IN_APP: {
			"subject": "புகார் பெறப்பட்டது: {{ subject }}",
			"body": "{{ grievance }} ({{ label(category) }}, {{ label(priority) }}){% if student_name %} - "
			"{{ student_name }}{% endif %}.",
		},
	},
	ev.GRIEVANCE_ASSIGNED: {
		ev.SMS: {
			"body": "புகார் {{ grievance }} ({{ label(category) }}, {{ label(priority) }}) உங்களுக்கு "
			"ஒதுக்கப்பட்டது: {{ short(subject, 25) }}. {{ fdatetime(sla_due_by) }} க்குள் பதிலளிக்கவும்."
			+ SIGN_OFF,
			"template_parameters": "grievance\nlabel(category)\nlabel(priority)\nshort(subject, 25)\n"
			"fdatetime(sla_due_by)",
		},
		ev.WHATSAPP: {
			"body": "{{ hostel }} விடுதியின் புகார் {{ grievance }} உங்களுக்கு ஒதுக்கப்பட்டுள்ளது. வகை: "
			"{{ label(category) }}. முன்னுரிமை: {{ label(priority) }}. தலைப்பு: {{ subject }}. "
			"{{ fdatetime(sla_due_by) }} க்குள் பதிலளிக்கவும்.",
			"template_parameters": "hostel\ngrievance\nlabel(category)\nlabel(priority)\nsubject\n"
			"fdatetime(sla_due_by)",
		},
		ev.EMAIL: {
			"subject": "உங்களுக்கு ஒதுக்கப்பட்ட புகார்: {{ subject }} ({{ grievance }})",
			"body": _email(
				"கீழ்க்கண்ட புகார் உங்களுக்கு ஒதுக்கப்பட்டுள்ளது.",
				_rows(
					("புகார்", "{{ grievance }}"),
					("விடுதி", "{{ hostel }}"),
					("வகை", "{{ label(category) }}"),
					("முன்னுரிமை", "{{ label(priority) }}"),
					("பதிலளிக்க வேண்டிய நேரம்", "{{ fdatetime(sla_due_by) }}"),
				),
				"{%% if %s %%}%s{%% endif %%}" % (GRIEVANCE_MESSAGE, _quote(GRIEVANCE_MESSAGE)),
				"மாணவருக்குத் தெரியும்படி, வேலை முன்னேறும்போது Hostel360 இல் புகாரைப் புதுப்பிக்கவும்.",
				action="புகாரைத் திற",
				greeting=COLLEAGUE,
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "ஒதுக்கப்பட்ட புகார்: {{ subject }}",
			"body": "{{ grievance }} ({{ label(category) }}, {{ label(priority) }}). "
			"{{ fdatetime(sla_due_by) }} க்குள் பதிலளிக்கவும்.",
		},
	},
	ev.GRIEVANCE_UPDATED: {
		ev.SMS: {
			"body": "புகார் {{ grievance }} ({{ short(subject, 25) }}) குறித்து புதிய தகவல் உள்ளது. நிலை: "
			"{{ label(status) }}. பார்க்க: {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "grievance\nshort(subject, 25)\nlabel(status)",
		},
		ev.WHATSAPP: {
			"body": "புகார் {{ grievance }} ({{ subject }}) குறித்து புதிய தகவல் உள்ளது. நிலை: "
			"{{ label(status) }}. தகவல்: {{ %s or 'விடுதி இணையதளத்தில் பார்க்கவும்' }}" % GRIEVANCE_MESSAGE,
			"template_parameters": "grievance\nsubject\nlabel(status)\n"
			f"{GRIEVANCE_MESSAGE} or 'விடுதி இணையதளத்தில் பார்க்கவும்'",
		},
		ev.EMAIL: {
			"subject": "புகார் {{ grievance }} குறித்த புதிய தகவல்: {{ subject }}",
			"body": _email(
				"புகார் {{ grievance }} ({{ subject }}) குறித்து புதிய தகவல் உள்ளது.",
				"{%% if %s %%}%s{%% endif %%}" % (GRIEVANCE_MESSAGE, _quote(GRIEVANCE_MESSAGE)),
				"தற்போதைய நிலை: <strong>{{ label(status) }}</strong>.",
				action="புகாரைப் பார்",
			),
		},
		ev.IN_APP: {
			"subject": "புகார் நிலவரம்: {{ subject }}",
			"body": "{{ %s or 'உங்கள் புகார் குறித்து புதிய தகவல் உள்ளது.' }} (நிலை: {{ label(status) }})"
			% GRIEVANCE_MESSAGE,
		},
	},
	ev.GRIEVANCE_ESCALATED: {
		ev.SMS: {
			"body": "{{ hostel }} விடுதியின் புகார் {{ grievance }} ({{ label(category) }}, "
			"{{ label(priority) }}) நிலை {{ escalation_level }} க்கு உயர்த்தப்பட்டது. இன்றே "
			"பரிசீலிக்கவும்." + SIGN_OFF,
			"template_parameters": "hostel\ngrievance\nlabel(category)\nlabel(priority)\nescalation_level",
		},
		ev.WHATSAPP: {
			"body": "{{ hostel }} விடுதியின் புகார் {{ grievance }} நிலை {{ escalation_level }} க்கு "
			"உயர்த்தப்பட்டுள்ளது. வகை: {{ label(category) }}. முன்னுரிமை: {{ label(priority) }}. தலைப்பு: "
			"{{ subject }}. இன்றே பரிசீலிக்கவும்.",
			"template_parameters": "hostel\ngrievance\nescalation_level\nlabel(category)\nlabel(priority)\n"
			"subject",
		},
		ev.EMAIL: {
			"subject": "உயர்த்தப்பட்ட புகார் {{ grievance }}: {{ subject }}",
			"body": _email(
				"புகார் {{ grievance }} உங்கள் கவனத்திற்கு உரியது; இது நிலை {{ escalation_level }} க்கு "
				"உயர்த்தப்பட்டுள்ளது.",
				_rows(
					("விடுதி", "{{ hostel }}"),
					("தலைப்பு", "{{ subject }}"),
					("வகை", "{{ label(category) }}"),
					("முன்னுரிமை", "{{ label(priority) }}"),
				),
				"இன்றே பரிசீலித்து மாணவருக்குத் தகவல் தெரிவிக்கவும்.",
				action="புகாரைத் திற",
				greeting=WARDEN,
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "புகார் உயர்த்தப்பட்டது: {{ subject }}",
			"body": "{{ hostel }} விடுதியின் {{ grievance }} இப்போது நிலை {{ escalation_level }} இல் உள்ளது.",
		},
	},
	ev.GRIEVANCE_RESOLVED: {
		ev.SMS: {
			"body": "உங்கள் புகார் {{ grievance }} ({{ short(subject, 25) }}) தீர்க்கப்பட்டது. தீர்வை "
			"மதிப்பிட: {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "grievance\nshort(subject, 25)",
		},
		ev.WHATSAPP: {
			"body": "புகார் {{ grievance }} ({{ subject }}) தீர்க்கப்பட்டது: {{ resolution or message }}. "
			"பிரச்சினை தீரவில்லை என்றால் விடுதி இணையதளத்தில் மீண்டும் திறக்கலாம்: {{ portal_url }}",
			"template_parameters": "grievance\nsubject\nresolution or message\nportal_url",
		},
		ev.EMAIL: {
			"subject": "புகார் {{ grievance }} தீர்க்கப்பட்டது: {{ subject }}",
			"body": _email(
				"புகார் {{ grievance }} ({{ subject }}) தீர்க்கப்பட்டதாகக் குறிக்கப்பட்டுள்ளது.",
				"{% if resolution or message %}" + _quote("resolution or message") + "{% endif %}",
				"தீர்வை மதிப்பிடச் சிறிது நேரம் ஒதுக்கவும். பிரச்சினை தீரவில்லை என்றால், இணையதளத்திலிருந்து "
				"புகாரை மீண்டும் திறக்கலாம்.",
				action="தீர்வை மதிப்பிடு",
			),
		},
		ev.IN_APP: {
			"subject": "புகார் தீர்க்கப்பட்டது: {{ subject }}",
			"body": "{{ resolution or message }} தீர்வை மதிப்பிடவும்; பிரச்சினை தொடர்ந்தால் புகாரை மீண்டும் "
			"திறக்கவும்.",
		},
	},
	ev.BONAFIDE_REQUESTED: {
		ev.EMAIL: {
			"subject": "போனஃபைட் சான்றிதழ் கோரிக்கை: {{ student_name }} ({{ request }})",
			"body": _email(
				"ஒரு போனஃபைட் சான்றிதழ் கோரிக்கை உங்கள் ஒப்புதலுக்காகக் காத்திருக்கிறது.",
				_rows(
					("கோரிக்கை", "{{ request }}"),
					("மாணவர்", "{{ student_name }}"),
					("விடுதி", "{{ hostel }}"),
					("நோக்கம்", "{{ label(purpose) }}"),
					("விவரங்கள்", "{{ purpose_details or '-' }}"),
				),
				APPROVALS_PAGE,
				action="கோரிக்கையைப் பார்",
				greeting=WARDEN,
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "பரிசீலிக்க வேண்டிய போனஃபைட் கோரிக்கை: {{ student_name }}",
			"body": "{{ request }}{% if hostel %} ({{ hostel }}){% endif %}: {{ label(purpose) }}"
			"{% if purpose_details %} - {{ purpose_details }}{% endif %}.",
		},
	},
	ev.BONAFIDE_DECIDED: {
		ev.SMS: {
			"body": "{{ short(student_name) }} அவர்களின் போனஃபைட் சான்றிதழ் கோரிக்கை {{ request }}: "
			"{{ label(status) }}. விவரம்: {{ portal_url }}" + SIGN_OFF,
			"template_parameters": "short(student_name)\nrequest\nlabel(status)",
		},
		ev.WHATSAPP: {
			"body": "{{ student_name }} அவர்களின் போனஃபைட் சான்றிதழ் கோரிக்கை {{ request }} - முடிவு: "
			"{{ label(status) }}. {{ 'சான்றிதழ் எண் ' ~ certificate_number if certificate_number else "
			"'காரணம்: ' ~ (rejection_reason or 'விடுதி அலுவலகத்தைத் தொடர்பு கொள்ளவும்') }}.",
			"template_parameters": "student_name\nrequest\nlabel(status)\n"
			"'சான்றிதழ் எண் ' ~ certificate_number if certificate_number else 'காரணம்: ' ~ "
			"(rejection_reason or 'விடுதி அலுவலகத்தைத் தொடர்பு கொள்ளவும்')",
		},
		ev.EMAIL: {
			"subject": "போனஃபைட் சான்றிதழ் கோரிக்கை {{ request }}: {{ label(status) }}",
			"body": _email(
				"{{ student_name }} அவர்களின் போனஃபைட் சான்றிதழ் கோரிக்கை {{ request }}"
				"{% if purpose %} ({{ label(purpose) }}){% endif %} குறித்த முடிவு: "
				"<strong>{{ label(status) }}</strong>.",
				"{% if certificate_number %}சான்றிதழ் எண் {{ certificate_number }} ஐ இணையதளத்தின் "
				"சான்றிதழ்கள் பக்கத்திலிருந்து பதிவிறக்கலாம்{% if valid_till %}; இது {{ fdate(valid_till) }} "
				"வரை செல்லுபடியாகும்{% endif %}.{% elif rejection_reason %}காரணம்: "
				"{{ rejection_reason }}{% endif %}",
				action="சான்றிதழ்களுக்குச் செல்",
			),
		},
		ev.IN_APP: {
			"subject": "போனஃபைட் சான்றிதழ்: {{ label(status) }}{% if student_name %} - {{ student_name }}"
			"{% endif %}",
			"body": "{{ request }}{% if certificate_number %}: சான்றிதழ் {{ certificate_number }} பதிவிறக்கத் "
			"தயாராக உள்ளது.{% elif rejection_reason %}: {{ rejection_reason }}{% else %}.{% endif %}",
		},
	},
	ev.SUPPORT_TICKET_UPDATE: {
		ev.SMS: {
			"body": "HyFib ஆதரவுச் சீட்டு {{ ticket }} இன் நிலை: {{ label(status) }} - {{ short(subject) }}. "
			"-HyFib Support",
			"template_parameters": "ticket\nlabel(status)\nshort(subject)",
		},
		ev.WHATSAPP: {
			"body": "HyFib ஆதரவுச் சீட்டு {{ ticket }} ({{ subject }}) இன் தற்போதைய நிலை: "
			"{{ label(status) }}. தகவல்: {{ %s or 'Hostel360 இல் சீட்டைப் பார்க்கவும்' }}" % TICKET_UPDATE,
			"template_parameters": "ticket\nsubject\nlabel(status)\n"
			f"{TICKET_UPDATE} or 'Hostel360 இல் சீட்டைப் பார்க்கவும்'",
		},
		ev.EMAIL: {
			"subject": "[{{ ticket }}] {{ subject }}: {{ label(status) }}",
			"body": _email(
				"ஆதரவுச் சீட்டு {{ ticket }} புதுப்பிக்கப்பட்டுள்ளது.",
				_rows(
					("தலைப்பு", "{{ subject }}"),
					(
						"நிலை",
						"{{ label(status) }}{% if previous_status %} (முன்பு {{ label(previous_status) }})"
						"{% endif %}",
					),
				),
				"{%% if %s %%}%s{%% endif %%}" % (TICKET_UPDATE, _quote(TICKET_UPDATE)),
				"Hostel360 இல் சீட்டில் பதிலளிக்கவும் அல்லது HyFib ஆதரவை {{ support_phone }} என்ற எண்ணில் "
				"அழைக்கவும்.",
				action="சீட்டைத் திற",
				greeting=COLLEAGUE,
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "சீட்டு {{ ticket }}: {{ label(status) }}",
			"body": "{{ subject }}{%% if %s %%}: {{ %s }}{%% endif %%}" % (TICKET_UPDATE, TICKET_UPDATE),
		},
	},
	ev.MESS_LOW_STOCK: {
		ev.SMS: {
			"body": "{{ short(mess, 20) }} இல் இருப்புக் குறைவு: {{ item_count }} பொருட்கள் மறுஆர்டர் "
			"அளவுக்குக் கீழே உள்ளன ({{ short(items_summary) }}). இண்டென்ட் எழுப்பவும்." + SIGN_OFF,
			"template_parameters": "short(mess, 20)\nitem_count\nshort(items_summary)",
		},
		ev.WHATSAPP: {
			"body": "{{ mess }} இல் இருப்புக் குறைவு: {{ item_count }} பொருட்கள் மறுஆர்டர் அளவுக்குக் கீழே "
			"உள்ளன; அவற்றில் {{ items_summary }}. Hostel360 இல் இண்டென்ட் எழுப்பவும்.",
			"template_parameters": "mess\nitem_count\nitems_summary",
		},
		ev.EMAIL: {
			"subject": "{{ mess }} இல் இருப்புக் குறைவு: {{ item_count }} பொருட்கள் மறுஆர்டர் அளவுக்குக் கீழே",
			"body": _email(
				"{{ mess }} இல் கீழ்க்கண்ட பொருட்கள் மறுஆர்டர் அளவிலோ அதற்குக் கீழோ உள்ளன.",
				'<table style="border-collapse:collapse;margin:12px 0">'
				'<tr><th style="text-align:left;padding:6px 12px 6px 0">பொருள்</th>'
				'<th style="text-align:right;padding:6px 12px">இருப்பு</th>'
				'<th style="text-align:right;padding:6px 0">மறுஆர்டர் அளவு</th></tr>'
				"{% for row in items %}<tr>"
				'<td style="padding:6px 12px 6px 0">{{ row.item }}</td>'
				'<td style="text-align:right;padding:6px 12px">{{ row.qty }} {{ row.uom }}</td>'
				'<td style="text-align:right;padding:6px 0">{{ row.reorder_level }} {{ row.uom }}</td>'
				'</tr>{% else %}<tr><td colspan="3">{{ items_summary }}</td></tr>{% endfor %}</table>',
				"பொருட்களை உரிய நேரத்தில் வாங்க, உணவகப் பொருள் கோரிக்கை (இண்டென்ட்) எழுப்பவும்.",
				action="Hostel360 ஐத் திற",
				greeting=MESS_MANAGER,
				staff=True,
			),
		},
		ev.IN_APP: {
			"subject": "இருப்புக் குறைவு: {{ mess }} இல் {{ item_count }} பொருட்கள்",
			"body": "{{ items_summary }}",
		},
	},
}
