// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Academic Program", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Students"), () =>
				frappe.set_route("List", "Hostel Student", { program: frm.doc.name })
			);
		}
	},

	degree_level(frm) {
		const durations = { UG: 4, PG: 2, Doctoral: 3, Diploma: 3 };
		if (frm.is_new() && durations[frm.doc.degree_level]) {
			frm.set_value("duration_years", durations[frm.doc.degree_level]);
		}
	},
});
