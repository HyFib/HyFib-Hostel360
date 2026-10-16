// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Academic Year", {
	refresh(frm) {
		if (frm.doc.is_current) {
			frm.dashboard.set_headline_alert(
				__("This is the current academic year. New students and allotments use it by default."),
				"green"
			);
		} else if (!frm.is_new()) {
			frm.add_custom_button(__("Make Current"), () => {
				frm.set_value("is_current", 1);
				frm.save();
			});
		}
	},

	start_date(frm) {
		if (!frm.doc.start_date) return;
		const start = frappe.datetime.str_to_obj(frm.doc.start_date);
		if (!frm.doc.academic_year) {
			frm.set_value("academic_year", `${start.getFullYear()}-${start.getFullYear() + 1}`);
		}
		if (!frm.doc.end_date) {
			frm.set_value(
				"end_date",
				frappe.datetime.add_days(frappe.datetime.add_months(frm.doc.start_date, 12), -1)
			);
		}
	},
});
