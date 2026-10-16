"""Fixed values (statuses, categories, payment modes) as they read in each template language.

Callers pass these values in English, exactly as stored on the documents. Templates show
them with ``label(value)``, which returns the wording below for the template's language,
then the site's own translation of the value, and finally the value unchanged. The Tamil
wording follows the student and parent portal, so a parent reads the same word in a
message as on the screen; each label stays within 30 characters, the most a DLT template
variable may carry.
"""

# DLT lets each {#var#} of a registered SMS carry at most 30 characters.
MAX_LABEL_LENGTH = 30

import frappe

from hostel360.hostel_communication.events import DEFAULT_LANGUAGE, TAMIL

LABELS = {
	TAMIL: {
		# leave, mess reduction, bonafide and fee statuses
		"Pending": "நிலுவையில் உள்ளது",
		"Pending Parent Approval": "பெற்றோர் ஒப்புதல் நிலுவையில்",
		"Pending Warden Approval": "காப்பாளர் ஒப்புதல் நிலுவையில்",
		"Approved": "ஒப்புதல் பெற்றது",
		"Rejected": "நிராகரிக்கப்பட்டது",
		"Cancelled": "ரத்து செய்யப்பட்டது",
		"Checked Out": "வெளியேறியது",
		"Returned": "திரும்பியது",
		"Overdue": "கெடு தாண்டியது",
		"Issued": "வழங்கப்பட்டது",
		"Unpaid": "செலுத்தப்படவில்லை",
		"Partly Paid": "பகுதி செலுத்தப்பட்டது",
		"Paid": "செலுத்தப்பட்டது",
		# grievance and support ticket statuses
		"Open": "திறந்துள்ளது",
		"Acknowledged": "ஏற்கப்பட்டது",
		"In Progress": "நடைபெறுகிறது",
		"Waiting on Institution": "நிறுவனப் பதில் நிலுவையில்",
		"Escalated": "உயர்நிலைக்கு அனுப்பப்பட்டது",
		"Resolved": "தீர்க்கப்பட்டது",
		"Closed": "மூடப்பட்டது",
		"Reopened": "மீண்டும் திறக்கப்பட்டது",
		# priorities
		"Low": "குறைவு",
		"Medium": "நடுத்தரம்",
		"High": "அதிகம்",
		"Urgent": "அவசரம்",
		"Critical": "மிக அவசரம்",
		# grievance categories
		"Room and Furniture": "அறை மற்றும் தளவாடங்கள்",
		"Electrical": "மின்சாரம்",
		"Plumbing": "குழாய் வேலை",
		"Cleanliness": "தூய்மை",
		"Mess and Food": "உணவகம் மற்றும் உணவு",
		"Water": "தண்ணீர்",
		"Internet": "இணையம்",
		"Security": "பாதுகாப்பு",
		"Health": "உடல்நலம்",
		"Ragging or Harassment": "ராகிங் அல்லது துன்புறுத்தல்",
		"Staff Behaviour": "பணியாளர் நடத்தை",
		"Other": "பிற",
		# invoice types and payment modes
		"Hostel Fee": "விடுதிக் கட்டணம்",
		"Mess Bill": "உணவகக் கட்டணம்",
		"Fine": "அபராதம்",
		"Online": "இணைய வழி",
		"Cash": "ரொக்கம்",
		"UPI": "UPI",
		"Demand Draft": "வரைவோலை",
		"Cheque": "காசோலை",
		"Bank Transfer": "வங்கிப் பரிமாற்றம்",
		# mess reduction reasons
		"Home Visit": "வீட்டுக்குச் செல்லுதல்",
		"Medical": "மருத்துவம்",
		"Internship": "பயிற்சிப் பணி (இன்டர்ன்ஷிப்)",
		"Sports or Cultural Event": "விளையாட்டு அல்லது கலை நிகழ்வு",
		"College Tour": "கல்விச் சுற்றுலா",
		# bonafide certificate purposes
		"Bank Loan": "வங்கிக் கடன்",
		"Scholarship": "கல்வி உதவித்தொகை",
		"Passport": "கடவுச்சீட்டு",
		"Visa": "விசா",
		"Address Proof": "முகவரிச் சான்று",
	},
}


def label(value, language: str | None = None) -> str:
	"""``value`` as it reads in ``language`` (English values are returned unchanged)."""
	if value is None:
		return ""
	text = str(value)
	language = language or DEFAULT_LANGUAGE
	if language == DEFAULT_LANGUAGE or not text.strip():
		return text
	known = LABELS.get(language, {}).get(text.strip())
	if known:
		return known
	try:
		return frappe._(text, lang=language)
	except Exception:
		return text
