// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mess Settings", {
	refresh(frm) {
		frm.set_intro(
			__("These rules apply to mess reduction applications made after you save. Existing applications are not affected."),
			"blue"
		);
	},
});
