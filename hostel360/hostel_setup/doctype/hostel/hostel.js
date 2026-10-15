// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel", {
	refresh(frm) {
		frm.set_query("warden", () => ({ filters: { enabled: 1, user_type: "System User" } }));

		if (frm.is_new()) return;
		// The title shows the hostel name, so keep the code (used in room numbers) in view.
		frm.toggle_display("hostel_code", true);

		const total = frm.doc.total_capacity || 0;
		const occupied = frm.doc.occupied_beds || 0;
		frm.dashboard.add_indicator(__("Beds: {0}", [total]), "blue");
		frm.dashboard.add_indicator(__("Occupied: {0}", [occupied]), occupied ? "orange" : "gray");
		frm.dashboard.add_indicator(
			__("Vacant: {0}", [frm.doc.vacant_beds || 0]),
			frm.doc.vacant_beds ? "green" : "red"
		);

		frm.add_custom_button(__("Room Board"), () =>
			frappe.set_route("room-board", { hostel: frm.doc.name })
		);
		frm.add_custom_button(
			__("Add Block"),
			() => frappe.new_doc("Hostel Block", { hostel: frm.doc.name }),
			__("Create")
		);
		frm.add_custom_button(
			__("Add Room"),
			() => frappe.new_doc("Hostel Room", { hostel: frm.doc.name }),
			__("Create")
		);
		frm.add_custom_button(
			__("Occupancy Report"),
			() => frappe.set_route("query-report", "Hostel Occupancy", { hostel: frm.doc.name }),
			__("View")
		);
		frm.add_custom_button(
			__("Residents"),
			() =>
				frappe.set_route("List", "Hostel Student", {
					current_hostel: frm.doc.name,
				}),
			__("View")
		);
		frm.add_custom_button(
			__("Recalculate Occupancy"),
			() =>
				frm.call({ doc: frm.doc, method: "recalculate_occupancy", freeze: true }).then(() => {
					frappe.show_alert({ message: __("Occupancy updated"), indicator: "green" });
					frm.reload_doc();
				}),
			__("Actions")
		);
	},
});
