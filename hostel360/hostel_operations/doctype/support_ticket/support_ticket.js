// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Support Ticket", {
	setup(frm) {
		frm.set_query("assigned_to", () => ({
			query: "hostel360.hostel_operations.support.operations_user_query",
		}));
	},

	refresh(frm) {
		if (frm.is_new()) {
			frm.set_intro(
				__("The HyFib helpdesk responds within 1 hour and resolves within 4 hours. For urgent issues also call {0}.", [
					frappe.boot.hostel360?.support_phone || "+91 96060 60596",
				]),
				"blue"
			);
			return;
		}
		show_sla(frm);
		add_ops_buttons(frm);
		add_raiser_buttons(frm);
	},
});

function show_sla(frm) {
	const doc = frm.doc;
	const closed = ["Resolved", "Closed"].includes(doc.status);
	const messages = [];
	let colour = "blue";
	if (doc.response_sla_breached) {
		messages.push(__("First response SLA breached"));
		colour = "red";
	} else if (!doc.first_response_on && doc.response_due_by && !closed) {
		messages.push(__("First response due {0}", [frappe.datetime.comment_when(doc.response_due_by)]));
	}
	if (doc.resolution_sla_breached) {
		messages.push(__("Resolution SLA breached"));
		colour = "red";
	} else if (!closed && doc.resolution_due_by) {
		messages.push(
			doc.status === "Waiting on Institution"
				? __("Resolution clock paused while waiting on the institution")
				: __("Resolution due {0}", [frappe.datetime.comment_when(doc.resolution_due_by)])
		);
	}
	if (closed) {
		messages.push(__("Resolved {0}", [frappe.datetime.comment_when(doc.resolved_on)]));
		colour = "green";
	}
	if (messages.length) {
		frm.dashboard.set_headline(messages.join(" · "), colour);
	}
}

function add_ops_buttons(frm) {
	if (!frappe.user.has_role(["HyFib Operations", "System Manager"])) {
		return;
	}
	const doc = frm.doc;
	const set_status = (status) => {
		frm.set_value("status", status);
		frm.save();
	};
	if (doc.status === "Open") {
		frm.add_custom_button(__("Acknowledge"), () => {
			if (!doc.assigned_to) {
				frm.set_value("assigned_to", frappe.session.user);
			}
			set_status("Acknowledged");
		}).addClass("btn-primary");
	}
	if (["Open", "Acknowledged", "Waiting on Institution"].includes(doc.status)) {
		frm.add_custom_button(__("Start Work"), () => set_status("In Progress"), __("Status"));
	}
	if (["Acknowledged", "In Progress"].includes(doc.status)) {
		frm.add_custom_button(__("Waiting on Institution"), () => set_status("Waiting on Institution"), __("Status"));
	}
	if (!["Resolved", "Closed"].includes(doc.status)) {
		frm.add_custom_button(
			__("Resolve"),
			() => {
				frappe.prompt(
					{ fieldname: "resolution_details", fieldtype: "Text Editor", label: __("Resolution"), reqd: 1 },
					(values) => {
						frm.set_value("resolution_details", values.resolution_details);
						set_status("Resolved");
					},
					__("Resolve Ticket"),
					__("Resolve")
				);
			},
			__("Status")
		);
	}
}

function add_raiser_buttons(frm) {
	const doc = frm.doc;
	if (doc.raised_by !== frappe.session.user && !frappe.user.has_role(["HyFib Operations", "System Manager"])) {
		return;
	}
	if (doc.status === "Resolved") {
		frm.add_custom_button(__("Confirm and Close"), () => {
			frm.call({ method: "close_ticket", doc: frm.doc, freeze: true }).then(() => frm.reload_doc());
		});
	}
	if (["Resolved", "Closed"].includes(doc.status)) {
		frm.add_custom_button(__("Re-open"), () => {
			frappe.prompt(
				{ fieldname: "reason", fieldtype: "Small Text", label: __("What is still wrong?"), reqd: 1 },
				(values) => {
					frm.call({ method: "reopen_ticket", doc: frm.doc, args: values, freeze: true }).then(() =>
						frm.reload_doc()
					);
				},
				__("Re-open Ticket"),
				__("Re-open")
			);
		});
	}
}
