// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mess Item", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(
			__("Stock Ledger"),
			() => frappe.set_route("List", "Mess Stock Ledger Entry", { item: frm.doc.name, is_cancelled: 0 }),
			__("View")
		);
		frm.add_custom_button(
			__("Consumption"),
			() => frappe.set_route("query-report", "Mess Consumption", { item: frm.doc.name }),
			__("View")
		);
	},
});
