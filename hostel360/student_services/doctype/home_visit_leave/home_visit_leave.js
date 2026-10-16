// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const H360_LEAVE_APPROVER_ROLES = ["Hostel Warden", "Deputy Warden", "Hostel Administrator", "System Manager"];
const H360_LEAVE_STAFF_ROLES = [...H360_LEAVE_APPROVER_ROLES, "Hostel Office Staff"];
// Gate staff only read leaves; they record check-out and return at the server's time.
const H360_LEAVE_GATE_ROLE = "Hostel Security";

frappe.ui.form.on("Home Visit Leave", {
	setup(frm) {
		frm.set_query("student", () => ({
			filters: { status: "Active", validation_status: "Validated" },
		}));
		frm.set_query("parent_guardian", () => ({
			query: "hostel360.student_services.doctype.home_visit_leave.home_visit_leave.guardian_query",
			filters: { student: frm.doc.student },
		}));
	},

	onload(frm) {
		// Leave entered at the desk is recorded on the student's behalf by hostel staff.
		if (frm.is_new() && frm.doc.applied_by === "Student") {
			frm.set_value("applied_by", "Warden");
		}
	},

	applied_by(frm) {
		frm.toggle_reqd("parent_guardian", frm.doc.applied_by === "Parent");
	},

	refresh(frm) {
		frm.toggle_reqd("parent_guardian", frm.is_new() && frm.doc.applied_by === "Parent");
		frm.toggle_enable("parent_guardian", frm.is_new());
		if (frm.is_new()) return;

		h360_leave.show_alerts(frm);
		h360_leave.add_actions(frm);
	},
});

const h360_leave = {
	show_alerts(frm) {
		const doc = frm.doc;
		if (doc.status === "Overdue") {
			frm.dashboard.set_headline_alert(
				__("Return overdue since {0}. Contact the student on {1}.", [
					frappe.datetime.str_to_user(doc.to_datetime),
					doc.contact_during_leave || __("the registered number"),
				]),
				"red"
			);
		} else if (doc.status === "Pending Parent Approval") {
			frm.dashboard.set_headline_alert(__("Waiting for the parent's approval on the portal."), "orange");
		} else if (doc.status === "Checked Out") {
			frm.dashboard.set_headline_alert(
				__("Away from the hostel. Expected back by {0}.", [frappe.datetime.str_to_user(doc.to_datetime)]),
				"blue"
			);
		}
	},

	add_actions(frm) {
		const doc = frm.doc;
		const is_approver = frappe.user.has_role(H360_LEAVE_APPROVER_ROLES);
		const is_staff = frappe.user.has_role(H360_LEAVE_STAFF_ROLES) && frm.perm[0]?.write;
		const at_gate = !is_staff && frappe.user.has_role(H360_LEAVE_GATE_ROLE);
		const can_move = is_staff || at_gate;

		if (is_approver && doc.status === "Pending Warden Approval") {
			frm.add_custom_button(__("Approve"), () => h360_leave.decide(frm, true)).addClass("btn-primary");
		}
		if (is_approver && ["Pending Warden Approval", "Pending Parent Approval"].includes(doc.status)) {
			frm.add_custom_button(__("Reject"), () => h360_leave.decide(frm, false));
		}
		if (can_move && doc.status === "Approved") {
			frm.add_custom_button(__("Checked Out"), () =>
				at_gate
					? h360_leave.record_now(
							frm,
							"mark_checked_out",
							__("Record that {0} has left the hostel now?", [h360_leave.student_label(frm)])
					  )
					: h360_leave.record_time(frm, "mark_checked_out", "checked_out_on", __("Checked Out At"))
			);
		}
		if (can_move && ["Checked Out", "Overdue"].includes(doc.status)) {
			frm.add_custom_button(__("Returned"), () =>
				at_gate
					? h360_leave.record_now(
							frm,
							"mark_returned",
							__("Record that {0} is back in the hostel now?", [h360_leave.student_label(frm)])
					  )
					: h360_leave.record_time(frm, "mark_returned", "returned_on", __("Returned At"))
			);
		}
		if (is_staff && doc.status === "Approved") {
			// A student who left without being recorded at the gate.
			frm.add_custom_button(
				__("Returned"),
				() => h360_leave.record_time(frm, "mark_returned", "returned_on", __("Returned At")),
				__("More")
			);
		}
		if (is_staff && ["Pending Parent Approval", "Pending Warden Approval", "Approved"].includes(doc.status)) {
			frm.add_custom_button(
				__("Cancel Leave"),
				() =>
					frappe.confirm(__("Cancel this leave?"), () =>
						frm.call({ method: "cancel_leave", doc, freeze: true }).then(() => frm.reload_doc())
					),
				__("More")
			);
		}
	},

	decide(frm, approve) {
		const dialog = new frappe.ui.Dialog({
			title: approve ? __("Approve Leave") : __("Reject Leave"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p class="text-muted">${frappe.utils.escape_html(frm.doc.student_name || "")}:
						${frappe.datetime.str_to_user(frm.doc.from_datetime)} &rarr;
						${frappe.datetime.str_to_user(frm.doc.to_datetime)}</p>`,
				},
				{
					fieldname: "remarks",
					fieldtype: "Small Text",
					label: approve ? __("Remarks (optional)") : __("Reason for rejection"),
					reqd: approve ? 0 : 1,
				},
			],
			primary_action_label: approve ? __("Approve") : __("Reject"),
			primary_action(values) {
				dialog.hide();
				frm.call({
					method: approve ? "warden_approve" : "warden_reject",
					doc: frm.doc,
					args: { remarks: values.remarks },
					freeze: true,
				}).then(() => frm.reload_doc());
			},
		});
		dialog.show();
	},

	student_label(frm) {
		return frappe.utils.escape_html(frm.doc.student_name || frm.doc.student || "");
	},

	record_now(frm, method, question) {
		frappe.confirm(question, () => frm.call({ method, doc: frm.doc, freeze: true }).then(() => frm.reload_doc()));
	},

	record_time(frm, method, argname, label) {
		frappe.prompt(
			[{ fieldname: "at", fieldtype: "Datetime", label, default: frappe.datetime.now_datetime(), reqd: 1 }],
			(values) =>
				frm
					.call({ method, doc: frm.doc, args: { [argname]: values.at }, freeze: true })
					.then(() => frm.reload_doc()),
			label,
			__("Save")
		);
	},
};
