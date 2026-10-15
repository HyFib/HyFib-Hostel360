"""Default records for Hostel Setup.

``install_defaults`` runs after install and on every migrate, so it only fills what is
missing and never overwrites values an administrator has changed.
"""

import frappe
from frappe.utils import getdate, nowdate

INSTITUTION_NAME = "Alagappa Chettiar Government College of Engineering and Technology"
INSTITUTION_SHORT_NAME = "ACGCET"
INSTITUTION_ADDRESS = "Karaikudi – 630 003, Sivagangai District, Tamil Nadu"
SUPPORT_PHONE = "+91 96060 60596"
SUPPORT_EMAIL = "support@hyfib.com"
PRIVACY_NOTICE_VERSION = "1.0"

PRIVACY_NOTICE = f"""
<h3>Privacy Notice — ACGCET Hostels (Hostel360)</h3>
<p><strong>Version {PRIVACY_NOTICE_VERSION}</strong>. This notice explains how your personal data is
handled in Hostel360, the hostel management system of {INSTITUTION_NAME}, Karaikudi, under the
Digital Personal Data Protection Act, 2023 ("the Act").</p>

<h4>1. Who is responsible</h4>
<p>{INSTITUTION_NAME} ("the Institution") decides why and how your personal data is processed and is
the Data Fiduciary. HyFib Solutions Private Limited operates Hostel360 for the Institution as its Data
Processor, under a written contract, and processes your data only on the Institution's instructions.
All data is stored in India.</p>

<h4>2. Personal data we collect</h4>
<ul>
<li><strong>Identity and academic details</strong>: name, register and roll number, programme,
department, year of study, gender, date of birth, photograph and blood group.</li>
<li><strong>Contact details</strong>: mobile number, e-mail address, postal address and an
emergency contact.</li>
<li><strong>Parent or guardian details</strong>: name, relationship, mobile number, e-mail address
and occupation.</li>
<li><strong>Hostel records</strong>: room and bed allotment, home visit leave, mess enrolment and
reductions, mess bills, grievances, feedback and certificates.</li>
<li><strong>Fee and payment records</strong>: invoices, receipts and payment references. Card, UPI
and bank credentials are handled by the payment gateway and are never stored in Hostel360.</li>
<li><strong>Health information</strong> you choose to share (for example allergies), visible only
to wardens and administrators.</li>
<li><strong>Security data</strong>: login times, IP address and device information.</li>
</ul>

<h4>3. Why we use it</h4>
<ul>
<li>Hostel admission, room allotment and day-to-day hostel administration.</li>
<li>Leave approvals, mess management, grievance redressal and certificates.</li>
<li>Fee collection, receipts and reminders.</li>
<li>Sending one-time passwords and service messages by SMS, WhatsApp and e-mail to you and your
registered parent or guardian.</li>
<li>Your safety and responding to medical or other emergencies.</li>
<li>Complying with law, audit requirements and the Institution's rules, and keeping the system
secure.</li>
</ul>
<p>We do not sell your data and do not use it for advertising.</p>

<h4>4. Consent</h4>
<p>We process your data on the basis of the consent you give by accepting this notice, and for the
legitimate uses the Act permits, such as complying with law and responding to a medical emergency.
If you are under 18 years of age, your parent or lawful guardian gives this consent on your behalf.
You may withdraw consent at any time by writing to the Grievance Officer; withdrawal does not affect
processing already done, and records the Institution must keep by law are retained.</p>

<h4>5. Who we share it with</h4>
<ul>
<li>Institution staff who need it for their duties (wardens, hostel office and mess staff).</li>
<li>Your registered parent or guardian, for leave, fees and hostel updates.</li>
<li>Service providers working for the Institution under contract: cloud hosting in India, SMS and
WhatsApp delivery and the payment gateway.</li>
<li>Government authorities, when the law requires it.</li>
</ul>

<h4>6. How long we keep it</h4>
<p>Hostel records are kept while you are a resident and afterwards only for as long as the
Institution's record-retention rules, audit and applicable law require. Fee and payment records
are kept for the period required by accounting and audit rules. One-time passwords expire within
minutes and are never stored. Security logs are kept for 180 days, as required by CERT-In
directions. Data that is no longer needed is erased.</p>

<h4>7. How we protect it</h4>
<p>Data is encrypted in transit and at rest, access is limited by role, every staff login uses
two-factor authentication, changes are recorded in an audit trail and backups are encrypted.
Personal data breaches are reported to the Data Protection Board of India and to affected persons
as the Act requires.</p>

<h4>8. Your rights</h4>
<ul>
<li>Obtain a summary of your personal data and how it is processed.</li>
<li>Have inaccurate or incomplete data corrected or completed. You can update your contact details
in the portal under <em>Profile</em>.</li>
<li>Have data erased when it is no longer needed, subject to legal retention.</li>
<li>Withdraw consent and have your grievances redressed.</li>
<li>Nominate another person to exercise these rights in case of death or incapacity.</li>
</ul>

<h4>9. Grievance Officer</h4>
<p>The Hostel Administrator, ACGCET Hostels, {INSTITUTION_ADDRESS}.<br>
For help with the portal, contact HyFib Solutions support at {SUPPORT_PHONE} or {SUPPORT_EMAIL}.<br>
We will respond within the period prescribed under the Act and its rules. If you are not satisfied
with the response, you may complain to the Data Protection Board of India.</p>

<h4>10. Changes to this notice</h4>
<p>When this notice changes, its version number changes and you will be asked to read and accept
it again at your next login.</p>
""".strip()

# Filled on first install; afterwards only blank text values are filled in.
TEXT_DEFAULTS = {
	"institution_name": INSTITUTION_NAME,
	"institution_short_name": INSTITUTION_SHORT_NAME,
	"institution_address": INSTITUTION_ADDRESS,
	"support_phone": SUPPORT_PHONE,
	"support_email": SUPPORT_EMAIL,
	"privacy_notice_version": PRIVACY_NOTICE_VERSION,
	"privacy_notice": PRIVACY_NOTICE,
}

FIRST_INSTALL_DEFAULTS = {
	"student_portal_enabled": 1,
	"parent_portal_enabled": 1,
	"leave_requires_parent_approval": 1,
	"leave_return_grace_hours": 2,
	"grievance_escalation_hours": 48,
	"grievance_urgent_escalation_hours": 4,
	"bonafide_validity_days": 90,
}

ROOM_FACILITIES = (
	("Cot", "Single cot for each resident."),
	("Mattress", "Mattress and pillow."),
	("Study Table", "Study table for each resident."),
	("Chair", "Chair for each resident."),
	("Cupboard", "Lockable cupboard or wardrobe."),
	("Ceiling Fan", ""),
	("Lighting", "Tube light or LED lighting."),
	("Power Sockets", "Sockets for charging and study lamps."),
	("Wi-Fi", "Campus wireless network."),
	("Attached Bathroom", ""),
	("Common Bathroom", ""),
	("Hot Water", "Solar or electric water heater."),
	("RO Drinking Water", "Purified drinking water point."),
	("Power Backup", "Generator or inverter backup."),
	("Balcony", ""),
	("CCTV in Corridors", "Corridor and entrance surveillance."),
)


def install_defaults() -> None:
	install_room_facilities()
	current_year = ensure_current_academic_year()
	install_settings(current_year)


def install_room_facilities() -> None:
	for facility_name, description in ROOM_FACILITIES:
		if frappe.db.exists("Room Facility", facility_name):
			continue
		frappe.get_doc(
			{"doctype": "Room Facility", "facility_name": facility_name, "description": description}
		).insert(ignore_permissions=True)


def ensure_current_academic_year() -> str | None:
	"""Make sure one academic year (June to May) is current; create it when there is none."""
	from hostel360.hostel_setup.doctype.hostel_academic_year.hostel_academic_year import (
		academic_year_for_date,
	)

	current = frappe.db.get_value("Hostel Academic Year", {"is_current": 1}, "name")
	if current:
		return current

	label, start_date, end_date = academic_year_for_date(nowdate())
	existing = label if frappe.db.exists("Hostel Academic Year", label) else None
	if not existing:
		existing = frappe.db.get_value(
			"Hostel Academic Year",
			{"start_date": ["<=", getdate()], "end_date": [">=", getdate()]},
			"name",
		)

	if existing:
		frappe.db.set_value("Hostel Academic Year", existing, "is_current", 1, update_modified=False)
		return existing

	doc = frappe.get_doc(
		{
			"doctype": "Hostel Academic Year",
			"academic_year": label,
			"start_date": start_date,
			"end_date": end_date,
			"is_current": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def install_settings(current_year: str | None) -> None:
	doctype = "Hostel Settings"
	first_install = not frappe.db.get_single_value(doctype, "institution_name")

	values = {}
	for fieldname, default in TEXT_DEFAULTS.items():
		if not frappe.db.get_single_value(doctype, fieldname):
			values[fieldname] = default

	if first_install:
		values.update(FIRST_INSTALL_DEFAULTS)

	if current_year and not frappe.db.get_single_value(doctype, "current_academic_year"):
		values["current_academic_year"] = current_year

	if values:
		frappe.db.set_single_value(doctype, values)
		frappe.clear_document_cache(doctype, doctype)
