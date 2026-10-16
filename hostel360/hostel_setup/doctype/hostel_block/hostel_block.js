// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Block", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Room Board"), () =>
			frappe.set_route("room-board", { hostel: frm.doc.hostel, block: frm.doc.name })
		);
		frm.add_custom_button(
			__("Add Floor"),
			() => frappe.new_doc("Hostel Floor", { block: frm.doc.name }),
			__("Create")
		);
		frm.add_custom_button(
			__("Add Room"),
			() => frappe.new_doc("Hostel Room", { hostel: frm.doc.hostel, block: frm.doc.name }),
			__("Create")
		);
	},
});
