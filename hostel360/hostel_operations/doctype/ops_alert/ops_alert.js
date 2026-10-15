// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const OPS_ALERT_COLOURS = { Critical: "red", Warning: "orange", Info: "blue" };

frappe.ui.form.on("Ops Alert", {
	refresh(frm) {
		set_alert_indicator(frm);
		show_alert_headline(frm);
		if (frm.is_new() || !frappe.user.has_role(["HyFib Operations", "System Manager"])) {
			return;
		}

		const status = frm.doc.status;
		if (status === "Open") {
			frm.add_custom_button(__("Acknowledge"), () => {
				call_alert_action(frm, "hostel360.api.ops.acknowledge", {}, __("Alert acknowledged"));
			}).addClass("btn-primary");
		}
		if (["Open", "Acknowledged", "Suppressed"].includes(status)) {
			frm.add_custom_button(__("Resolve"), () => {
				frappe.prompt(
					{
						fieldname: "notes",
						fieldtype: "Small Text",
						label: __("What fixed it?"),
						reqd: 1,
					},
					(values) => {
						call_alert_action(frm, "hostel360.api.ops.resolve", values, __("Alert resolved"));
					},
					__("Resolve Alert"),
					__("Resolve")
				);
			});
		}
		if (["Open", "Acknowledged"].includes(status)) {
			frm.add_custom_button(
				__("Suppress"),
				() => {
					frappe.prompt(
						{
							fieldname: "notes",
							fieldtype: "Small Text",
							label: __("Why is this alert being silenced?"),
							reqd: 1,
						},
						(values) => {
							call_alert_action(frm, "hostel360.api.ops.suppress", values, __("Alert suppressed"));
						},
						__("Suppress Alert"),
						__("Suppress")
					);
				},
				__("More")
			);
		}
	},
});

function set_alert_indicator(frm) {
	const doc = frm.doc;
	if (doc.status === "Resolved") {
		frm.page.set_indicator(__("Resolved"), "green");
	} else if (doc.status === "Suppressed") {
		frm.page.set_indicator(__("Suppressed"), "gray");
	} else if (doc.status === "Acknowledged") {
		frm.page.set_indicator(__("{0} - Acknowledged", [__(doc.severity)]), "yellow");
	} else if (doc.status) {
		frm.page.set_indicator(__("{0} - Open", [__(doc.severity)]), OPS_ALERT_COLOURS[doc.severity] || "orange");
	}
}

function show_alert_headline(frm) {
	const doc = frm.doc;
	if (frm.is_new() || !doc.first_seen) {
		return;
	}
	const parts = [];
	if (doc.is_outage && doc.status !== "Resolved") {
		parts.push(`<strong>${__("Outage")}</strong>`);
	}
	parts.push(
		__("Seen {0} times since {1}", [
			doc.occurrence_count || 1,
			frappe.datetime.str_to_user(doc.first_seen),
		])
	);
	if (doc.escalation_level) {
		parts.push(__("escalated to level {0}", [doc.escalation_level]));
	}
	if (doc.status === "Resolved" && doc.duration_minutes !== undefined) {
		parts.push(__("open for {0} minutes", [doc.duration_minutes]));
	}
	frm.dashboard.set_headline(parts.join(" · "), doc.status === "Resolved" ? "green" : OPS_ALERT_COLOURS[doc.severity]);
}

function call_alert_action(frm, method, args, message) {
	frappe.call({
		method,
		args: { name: frm.doc.name, ...args },
		freeze: true,
		callback() {
			frappe.show_alert({ message, indicator: "green" });
			frm.reload_doc();
		},
	});
}
