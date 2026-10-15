// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mess Extra Charge", {
	setup(frm) {
		frm.set_query("student", () => ({ filters: { status: "Active" } }));
		frm.set_query("mess", () => ({ filters: { is_active: 1 } }));
	},

	refresh(frm) {
		if (frm.doc.status === "Billed" && frm.doc.mess_bill) {
			frm.set_intro(__("Billed in {0}.", [`<b>${frappe.utils.escape_html(frm.doc.mess_bill)}</b>`]), "green");
			frm.disable_form();
		}
	},
});
