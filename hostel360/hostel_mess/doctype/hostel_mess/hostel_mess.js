// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Mess", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(
			__("Stock Balance"),
			() => frappe.set_route("query-report", "Mess Stock Balance", { mess: frm.doc.name }),
			__("View")
		);
		frm.add_custom_button(
			__("Cost Summary"),
			() => frappe.set_route("query-report", "Mess Cost Summary", { mess: frm.doc.name }),
			__("View")
		);
		frm.add_custom_button(
			__("Menus"),
			() => frappe.set_route("List", "Mess Menu", { mess: frm.doc.name }),
			__("View")
		);
		if (!frm.doc.is_active) {
			frm.set_intro(__("This mess is inactive. Its residents cannot apply for reductions or be billed."), "orange");
		}
	},
});
