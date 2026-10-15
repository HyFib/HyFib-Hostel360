// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Feedback", {
	setup(frm) {
		frm.set_query("student", () => ({
			filters: { status: "Active", validation_status: "Validated" },
		}));
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.period) {
			frm.set_value("period", frappe.datetime.get_today().slice(0, 7));
		}
	},

	refresh(frm) {
		frm.toggle_reqd("student", frm.is_new());
		if (!frm.is_new() && frm.doc.is_anonymous) {
			frm.dashboard.set_headline_alert(__("Anonymous feedback: the student is not recorded."), "blue");
		}
	},
});
