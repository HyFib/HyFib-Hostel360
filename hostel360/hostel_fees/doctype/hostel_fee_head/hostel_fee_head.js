// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Fee Head", {
	refresh(frm) {
		if (!frm.doc.is_active) {
			frm.dashboard.set_headline(
				__("This fee head is inactive and cannot be used on new invoices or fee structures."),
				"orange"
			);
		}
	},
	fee_category(frm) {
		if (frm.doc.fee_category === "Deposit" && frm.is_new()) {
			frm.set_value("is_refundable", 1);
		}
	},
});
