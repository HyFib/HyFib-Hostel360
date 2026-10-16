// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mess Supplier", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(
			__("Receipts"),
			() => frappe.set_route("List", "Mess Stock Entry", { supplier: frm.doc.name, entry_type: "Receipt" }),
			__("View")
		);
	},
});
