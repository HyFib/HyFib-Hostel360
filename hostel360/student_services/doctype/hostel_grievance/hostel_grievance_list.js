// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Hostel Grievance"] = {
	add_fields: ["status", "priority", "is_confidential", "escalation_level"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		const colours = {
			Open: "orange",
			"In Progress": "blue",
			Escalated: "red",
			Reopened: "orange",
			Resolved: "green",
			Closed: "darkgrey",
		};
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
	formatters: {
		priority(value) {
			return value === "Urgent" ? `<span class="text-danger bold">${__(value)}</span>` : __(value);
		},
	},
};
