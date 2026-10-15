"""Default fee heads and payment settings (idempotent; runs after every migrate)."""

import frappe

from hostel360.hostel_fees.constants import FEE_HEAD, SETTINGS

# name, category, refundable, description
DEFAULT_FEE_HEADS = (
	("Hostel Rent", "Hostel", 0, "Room rent for the period."),
	("Establishment Charges", "Hostel", 0, "Maintenance, housekeeping, security and common amenities."),
	("Electricity Charges", "Hostel", 0, "Electricity consumed in rooms and common areas."),
	(
		"Caution Deposit",
		"Deposit",
		1,
		"Refundable deposit, adjusted against damages when the student vacates.",
	),
	("Mess Advance", "Mess", 0, "Advance adjusted against monthly mess bills."),
	("Mess Charges", "Mess", 0, "Monthly mess bill under the dividing system."),
	("Mess Extras", "Mess", 0, "Special meals and extra items billed with the mess bill."),
	("Fine", "Fine", 0, "Fines levied by the warden."),
	("Other", "Other", 0, "Other charges."),
)

# Only applied when the field has never been set, so administrators' choices survive migrations.
SETTINGS_DEFAULTS = {
	"gateway": "Razorpay",
	"test_mode": 1,
	"transaction_expiry_minutes": 60,
	"send_fee_reminders": 1,
	"reminder_days_before_due": 3,
	"reminder_interval_days": 3,
	"max_reminders_per_invoice": 0,
}


def install_defaults() -> None:
	if not frappe.db.exists("DocType", FEE_HEAD):
		return
	for name, category, refundable, description in DEFAULT_FEE_HEADS:
		if frappe.db.exists(FEE_HEAD, name):
			continue
		frappe.get_doc(
			{
				"doctype": FEE_HEAD,
				"fee_head_name": name,
				"fee_category": category,
				"is_refundable": refundable,
				"is_active": 1,
				"description": description,
			}
		).insert(ignore_permissions=True)

	install_payment_settings_defaults()


def install_payment_settings_defaults() -> None:
	if not frappe.db.exists("DocType", SETTINGS):
		return
	stored = {
		row.field
		for row in frappe.db.sql(
			"select field from `tabSingles` where doctype = %(doctype)s", {"doctype": SETTINGS}, as_dict=True
		)
	}
	values = {field: value for field, value in SETTINGS_DEFAULTS.items() if field not in stored}
	if "merchant_display_name" not in stored:
		values["merchant_display_name"] = _default_display_name()
	for field, value in values.items():
		frappe.db.set_single_value(SETTINGS, field, value)
	if values:
		frappe.clear_document_cache(SETTINGS, SETTINGS)


def _default_display_name() -> str:
	from hostel360.hostel_fees.utils import get_hostel_setting

	short_name = get_hostel_setting("institution_short_name")
	return f"{short_name} Hostels" if short_name else "Hostel Fees"
