// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Academic Department", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Programs"), () =>
				frappe.set_route("List", "Academic Program", { department: frm.doc.name })
			);
			frm.add_custom_button(__("Students"), () =>
				frappe.set_route("List", "Hostel Student", { department: frm.doc.name })
			);
		}
	},
});
