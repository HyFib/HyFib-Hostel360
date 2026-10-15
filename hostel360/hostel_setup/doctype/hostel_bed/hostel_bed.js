// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Bed", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.set_df_property(
			"status",
			"options",
			frm.doc.status === "Occupied"
				? ["Occupied"]
				: ["Vacant", "Reserved", "Maintenance"]
		);
		frm.toggle_enable("status", frm.doc.status !== "Occupied");

		if (frm.doc.current_allotment) {
			frm.add_custom_button(__("Open Allotment"), () =>
				frappe.set_route("Form", "Room Allotment", frm.doc.current_allotment)
			);
			frm.add_custom_button(__("Open Student"), () =>
				frappe.set_route("Form", "Hostel Student", frm.doc.current_student)
			);
		} else if (frm.doc.is_active && frm.doc.status === "Vacant") {
			frm.add_custom_button(__("Allot this Bed"), () =>
				frappe.new_doc("Room Allotment", {
					hostel: frm.doc.hostel,
					room: frm.doc.room,
					bed: frm.doc.name,
				})
			);
		}
		frm.add_custom_button(__("Room Board"), () =>
			frappe.set_route("room-board", { hostel: frm.doc.hostel })
		);
	},
});
