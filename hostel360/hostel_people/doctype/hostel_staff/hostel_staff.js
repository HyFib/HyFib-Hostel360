// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const H360_SCOPED_DESIGNATIONS = ["Deputy Warden", "Office Staff", "Mess Staff"];
const H360_DESK_DESIGNATIONS = [
	"Hostel Administrator",
	"Warden",
	"Deputy Warden",
	"Office Staff",
	"Mess Manager",
	"Mess Staff",
];

frappe.ui.form.on("Hostel Staff", {
	setup(frm) {
		frm.set_query("block", "assignments", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return { filters: { hostel: row.hostel || "" } };
		});
	},

	refresh(frm) {
		frm.trigger("toggle_login_fields");
		if (frm.is_new()) return;

		if (frm.doc.user) {
			frm.add_custom_button(
				__("Open User"),
				() => frappe.set_route("Form", "User", frm.doc.user),
				__("Desk Login")
			);
			frm.add_custom_button(
				__("Resend Welcome Email"),
				() =>
					frm
						.call({ doc: frm.doc, method: "resend_welcome_email", freeze: true })
						.then(() =>
							frappe.show_alert({ message: __("Welcome email queued"), indicator: "green" })
						),
				__("Desk Login")
			);
			frm.dashboard.set_headline_alert(
				frm.doc.is_active
					? __("Desk login {0} is active. Roles and hostel access follow this record.", [
							frappe.utils.escape_html(frm.doc.user),
					  ])
					: __("Desk login {0} is disabled because this staff member is inactive.", [
							frappe.utils.escape_html(frm.doc.user),
					  ]),
				frm.doc.is_active ? "green" : "orange"
			);
		}
	},

	designation(frm) {
		frm.trigger("toggle_login_fields");
	},

	toggle_login_fields(frm) {
		const can_login = H360_DESK_DESIGNATIONS.includes(frm.doc.designation);
		frm.toggle_display("create_user", can_login || frm.doc.user);
		frm.set_df_property(
			"assignments_section",
			"description",
			H360_SCOPED_DESIGNATIONS.includes(frm.doc.designation)
				? __("Required: this designation only sees records of the hostels listed here.")
				: __("For reference. Wardens, administrators and mess managers see all hostels.")
		);
		frm.toggle_reqd("email", !!frm.doc.create_user);
	},

	create_user(frm) {
		frm.toggle_reqd("email", !!frm.doc.create_user);
	},
});
