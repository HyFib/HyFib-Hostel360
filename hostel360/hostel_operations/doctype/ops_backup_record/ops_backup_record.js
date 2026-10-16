// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Ops Backup Record", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.page.set_indicator(__(frm.doc.status), frm.doc.status === "Success" ? "green" : "red");
		}
	},
});
