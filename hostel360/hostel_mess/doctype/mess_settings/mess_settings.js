// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mess Settings", {
	refresh(frm) {
		frm.set_intro(
			__(
				"Changes apply from the moment you save: to reduction applications made after that, and to indents and receipts still to be approved or received."
			),
			"blue"
		);
	},
});
