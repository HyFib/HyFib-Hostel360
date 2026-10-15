// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

// Fallback until the server answers; the server's DESIGNATION_ROLES is the source of truth.
const H360_DESIGNATION_RULES = {
	login: ["Deputy Warden", "Hostel Administrator", "Mess Manager", "Mess Staff", "Office Staff", "Warden"],
	scoped: ["Deputy Warden", "Mess Staff", "Office Staff"],
};

frappe.ui.form.on("Hostel Staff", {
	setup(frm) {
		frm.set_query("block", "assignments", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return { filters: { hostel: row.hostel || "" } };
		});
		frm.h360_rules = H360_DESIGNATION_RULES;
		frappe
			.xcall("hostel360.hostel_people.doctype.hostel_staff.hostel_staff.get_designation_rules")
			.then((rules) => {
				frm.h360_rules = rules;
				frm.trigger("toggle_login_fields");
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
		}
	},

	designation(frm) {
		frm.trigger("toggle_login_fields");
	},

	toggle_login_fields(frm) {
		const rules = frm.h360_rules || H360_DESIGNATION_RULES;
		const designation = frm.doc.designation;
		const can_login = rules.login.includes(designation);
		const user = frappe.utils.escape_html(frm.doc.user || "");

		frm.toggle_display("create_user", can_login || !!frm.doc.user);
		frm.set_df_property(
			"assignments_section",
			"description",
			rules.scoped.includes(designation)
				? __(
						"Required: this designation only sees records of the hostels listed here. Add a block to limit it to the students of that block."
				  )
				: __("For reference. Wardens, administrators and mess managers see all hostels.")
		);
		frm.toggle_reqd("email", !!frm.doc.create_user && can_login);

		frm.dashboard.clear_headline();
		if (designation && !can_login) {
			frm.dashboard.set_headline_alert(
				frm.doc.user
					? __(
							"{0} staff do not use the desk. The desk login {1} is disabled when this record is saved.",
							[__(designation), user]
					  )
					: __(
							"{0} staff are recorded for contact purposes only. They do not get a desk login.",
							[__(designation)]
					  ),
				"blue"
			);
		} else if (frm.doc.user && !frm.is_new()) {
			frm.dashboard.set_headline_alert(
				frm.doc.is_active
					? __("Desk login {0} is active. Roles and hostel access follow this record.", [user])
					: __("Desk login {0} is disabled because this staff member is inactive.", [user]),
				frm.doc.is_active ? "green" : "orange"
			);
		}
	},

	create_user(frm) {
		frm.trigger("toggle_login_fields");
	},
});
