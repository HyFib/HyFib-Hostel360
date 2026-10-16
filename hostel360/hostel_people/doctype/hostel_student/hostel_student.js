// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Student", {
	setup(frm) {
		frm.set_query("program", () => ({ filters: { is_active: 1 } }));
		frm.set_query("academic_year", () => ({ order_by: "start_date desc" }));
		// A parent may already be registered for a child in another hostel, whom this desk
		// cannot see: typing the parent's exact mobile number finds them for linking.
		frm.set_query("guardian", "guardians", () => ({
			query: "hostel360.hostel_people.queries.guardian_link_query",
		}));
	},

	refresh(frm) {
		if (frm.is_new()) return;
		// The form title shows the student's name, so keep the register number in view.
		frm.toggle_display("register_number", true);
		frm.trigger("show_status_banner");
		frm.trigger("add_verification_buttons");
		frm.trigger("add_residence_buttons");

		if (!frm.doc.user && frm.perm[0]?.write) {
			frm.add_custom_button(
				__("Create Portal User"),
				() =>
					frm
						.call({ doc: frm.doc, method: "create_portal_user", freeze: true })
						.then((r) => {
							const message =
								frm.doc.validation_status === "Validated"
									? __("Portal user {0} created. The student signs in with an OTP sent to {1}.", [
											r.message,
											frm.doc.mobile_number,
										])
									: __(
											"Portal user {0} created. The student can sign in with an OTP sent to {1} once the registration is validated.",
											[r.message, frm.doc.mobile_number]
										);
							frappe.show_alert({ message, indicator: "green" });
							frm.reload_doc();
						}),
				__("Actions")
			);
		}
		if (frm.doc.__onload?.can_unlock_sign_in) {
			frm.add_custom_button(__("Unlock Sign-in"), () => frm.trigger("unlock_sign_in"), __("Actions"));
		}
	},

	unlock_sign_in(frm) {
		// The lock follows repeated wrong codes and otherwise lasts an hour.
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save your changes first. The saved mobile number is the one that gets unlocked."));
			return;
		}
		const esc = frappe.utils.escape_html;
		const mobile = frm.doc.mobile_number;
		frappe.confirm(
			__(
				"Let {0} sign in again on {1} straight away? Do this only after confirming who you are speaking to, for example by calling back on the registered number.",
				[esc(frm.doc.full_name), esc(mobile)]
			),
			() =>
				frappe
					.call({ method: "hostel360.api.auth.unlock_sign_in", args: { mobile }, freeze: true })
					.then(() =>
						frappe.show_alert({
							message: __("Sign-in unlocked for {0}. A new code can be requested now.", [esc(mobile)]),
							indicator: "green",
						})
					)
		);
	},

	show_status_banner(frm) {
		const doc = frm.doc;
		if (doc.validation_status === "Pending") {
			frm.dashboard.set_headline_alert(
				__("Registration is pending validation. Check the details against the college register and validate them. Only then can the student sign in to the portal, confirm the details and accept the privacy notice."),
				"orange"
			);
		} else if (doc.validation_status === "Rejected") {
			frm.dashboard.set_headline_alert(
				__("Registration rejected: {0}", [frappe.utils.escape_html(doc.rejection_reason || "")]),
				"red"
			);
		} else if (doc.current_bed) {
			frm.dashboard.set_headline_alert(
				__("Resident of {0}, room {1}, bed {2}.", [
					frappe.utils.escape_html(doc.current_hostel),
					frappe.utils.escape_html(doc.current_room),
					frappe.utils.escape_html(doc.current_bed),
				]),
				"green"
			);
		}
	},

	add_verification_buttons(frm) {
		if (!frm.perm[0]?.write) return;
		if (frm.doc.validation_status !== "Validated") {
			frm.add_custom_button(
				__("Validate"),
				() =>
					frappe.confirm(
						__("Confirm that {0}'s details match the admission records?", [
							frappe.utils.escape_html(frm.doc.full_name),
						]),
						() =>
							frm
								.call({ doc: frm.doc, method: "mark_validated", freeze: true })
								.then(() => frm.reload_doc())
					),
				__("Verification")
			);
		}
		if (frm.doc.validation_status !== "Rejected" && !frm.doc.current_allotment) {
			frm.add_custom_button(
				__("Reject"),
				() => {
					frappe.prompt(
						{
							fieldname: "reason",
							fieldtype: "Small Text",
							label: __("Reason"),
							reqd: 1,
							description: __("Shown to office staff. Tell the student what to correct."),
						},
						(values) =>
							frm
								.call({
									doc: frm.doc,
									method: "mark_rejected",
									args: { reason: values.reason },
									freeze: true,
								})
								.then(() => frm.reload_doc()),
						__("Reject Registration"),
						__("Reject")
					);
				},
				__("Verification")
			);
		}
	},

	add_residence_buttons(frm) {
		if (frm.doc.current_allotment) {
			frm.add_custom_button(
				__("Current Allotment"),
				() => frappe.set_route("Form", "Room Allotment", frm.doc.current_allotment),
				__("View")
			);
		} else if (["Active", "Applied"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Allot Room"),
				() => frappe.new_doc("Room Allotment", { student: frm.doc.name }),
				__("Create")
			);
		}
		frm.add_custom_button(
			__("Allotment History"),
			() =>
				frappe.set_route("query-report", "Room Allotment History", {
					student: frm.doc.name,
				}),
			__("View")
		);
	},

	first_name(frm) {
		frm.trigger("set_full_name");
	},

	last_name(frm) {
		frm.trigger("set_full_name");
	},

	set_full_name(frm) {
		frm.set_value(
			"full_name",
			[frm.doc.first_name, frm.doc.last_name].filter(Boolean).join(" ").trim()
		);
	},
});

frappe.ui.form.on("Hostel Student Guardian", {
	is_primary(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.is_primary) return;
		(frm.doc.guardians || []).forEach((other) => {
			if (other.name !== row.name && other.is_primary) {
				frappe.model.set_value(other.doctype, other.name, "is_primary", 0);
			}
		});
	},

	guardian(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.guardian || row.relation) return;
		frappe.db.get_value("Hostel Guardian", row.guardian, "relation").then((r) => {
			if (r.message?.relation) frappe.model.set_value(cdt, cdn, "relation", r.message.relation);
		});
	},
});
