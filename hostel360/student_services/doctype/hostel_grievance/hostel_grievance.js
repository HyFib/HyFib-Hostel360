// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Grievance", {
	setup(frm) {
		frm.set_query("student", () => ({ filters: { status: "Active" } }));
	},

	refresh(frm) {
		if (frm.is_new()) return;
		h360_grievance.guard_confidentiality(frm);
		h360_grievance.show_alerts(frm);
		h360_grievance.add_actions(frm);
	},
});

const h360_grievance = {
	OPEN: ["Open", "In Progress", "Escalated", "Reopened"],
	CONFIDENTIAL_ROLES: ["Hostel Warden", "Hostel Administrator", "System Manager"],

	guard_confidentiality(frm) {
		// The server refuses the change too; this only saves the user a failed save.
		if (frappe.user.has_role(this.CONFIDENTIAL_ROLES)) return;
		frm.toggle_enable("is_confidential", !frm.doc.is_confidential);
		frm.toggle_enable("category", frm.doc.category !== "Ragging or Harassment");
	},

	show_alerts(frm) {
		const doc = frm.doc;
		if (doc.is_confidential) {
			frm.dashboard.add_comment(
				__("Confidential: only the Warden, hostel administrators and the assigned staff member can see this grievance."),
				"red",
				true
			);
		}
		if (this.OPEN.includes(doc.status) && doc.sla_due_by) {
			const overdue = frappe.datetime.str_to_obj(doc.sla_due_by) < new Date();
			frm.dashboard.set_headline_alert(
				overdue
					? __("Action was due by {0}.", [frappe.datetime.str_to_user(doc.sla_due_by)])
					: __("Action due by {0}.", [frappe.datetime.str_to_user(doc.sla_due_by)]),
				overdue ? "red" : "blue"
			);
		}
	},

	add_actions(frm) {
		const doc = frm.doc;
		const can_act = frm.perm[0]?.write || doc.assigned_to === frappe.session.user;
		if (!can_act) return;

		if (this.OPEN.includes(doc.status)) {
			frm.add_custom_button(__("Resolve"), () => this.resolve(frm)).addClass("btn-primary");
			frm.add_custom_button(__("Assign"), () => this.assign(frm));
			frm.add_custom_button(__("Add Update"), () => this.add_update(frm));
		} else if (doc.status === "Resolved") {
			frm.add_custom_button(__("Add Update"), () => this.add_update(frm));
		}
		if (doc.status !== "Closed") {
			frm.add_custom_button(__("Close"), () => this.close(frm), __("More"));
		}
	},

	call(frm, method, args) {
		return frm.call({ method, doc: frm.doc, args, freeze: true }).then(() => frm.reload_doc());
	},

	assign(frm) {
		frappe.prompt(
			[
				{
					fieldname: "user",
					fieldtype: "Link",
					options: "User",
					label: __("Assign To"),
					reqd: 1,
					default: frm.doc.assigned_to,
					get_query: () => ({
						query: "hostel360.student_services.doctype.hostel_grievance.hostel_grievance.staff_user_query",
					}),
				},
				{
					fieldname: "note",
					fieldtype: "Small Text",
					label: __("Internal note for the assignee (optional)"),
				},
			],
			(values) => this.call(frm, "assign", values),
			__("Assign Grievance"),
			__("Assign")
		);
	},

	add_update(frm) {
		const fields = [
			{ fieldname: "message", fieldtype: "Small Text", label: __("Update"), reqd: 1 },
			{
				fieldname: "is_internal",
				fieldtype: "Check",
				label: __("Internal note (not shown to the student)"),
				default: 0,
			},
		];
		if (["Open", "Reopened", "Escalated"].includes(frm.doc.status)) {
			fields.push({
				fieldname: "mark_in_progress",
				fieldtype: "Check",
				label: __("Mark as In Progress"),
				default: frm.doc.status !== "Escalated" ? 1 : 0,
			});
		}
		frappe.prompt(
			fields,
			(values) =>
				this.call(frm, "add_update", {
					message: values.message,
					is_internal: values.is_internal,
					status: values.mark_in_progress ? "In Progress" : null,
				}),
			__("Add Update"),
			__("Save")
		);
	},

	resolve(frm) {
		frappe.prompt(
			[
				{
					fieldname: "resolution",
					fieldtype: "Small Text",
					label: __("What was done"),
					description: __("The student sees this and can reopen the grievance within 7 days."),
					reqd: 1,
				},
			],
			(values) => this.call(frm, "resolve", values),
			__("Resolve Grievance"),
			__("Resolve")
		);
	},

	close(frm) {
		const needs_reason = frm.doc.status !== "Resolved";
		frappe.prompt(
			[
				{
					fieldname: "remarks",
					fieldtype: "Small Text",
					label: needs_reason ? __("Why is it being closed without a resolution?") : __("Remarks (optional)"),
					reqd: needs_reason ? 1 : 0,
				},
			],
			(values) => this.call(frm, "close", values),
			__("Close Grievance"),
			__("Close")
		);
	},
};
