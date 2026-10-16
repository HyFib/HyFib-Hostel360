// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Settings", {
	refresh(frm) {
		frm.set_query("current_academic_year", () => ({ order_by: "start_date desc" }));
		frm.add_custom_button(__("Room Board"), () => frappe.set_route("room-board"));
	},

	privacy_notice_version(frm) {
		if (!frm.is_dirty()) return;
		frappe.show_alert(
			{
				message: __(
					"After saving, every student and parent is asked to accept the new notice at their next login."
				),
				indicator: "orange",
			},
			8
		);
	},
});
