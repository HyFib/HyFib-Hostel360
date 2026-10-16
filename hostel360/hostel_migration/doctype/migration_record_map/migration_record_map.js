// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Migration Record Map", {
	refresh(frm) {
		const { target_doctype, target_name, migration_run } = frm.doc;
		if (target_doctype && target_name) {
			frappe.model.with_doctype(target_doctype, () => {
				// Rows of child tables (e.g. a student's guardians) have no form of their own.
				if (frappe.get_meta(target_doctype).istable) return;
				frm.add_custom_button(__("Open {0}", [__(target_doctype)]), () =>
					frappe.set_route("Form", target_doctype, target_name)
				);
			});
		}
		if (migration_run) {
			frm.add_custom_button(__("Migration Run"), () => frappe.set_route("Form", "Migration Run", migration_run));
		}
	},
});
