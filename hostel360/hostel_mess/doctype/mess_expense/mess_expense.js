// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mess Expense", {
	setup(frm) {
		frm.set_query("mess", () => ({ filters: { is_active: 1 } }));
		frm.set_query("supplier", () => ({ filters: { is_active: 1 } }));
	},
});
