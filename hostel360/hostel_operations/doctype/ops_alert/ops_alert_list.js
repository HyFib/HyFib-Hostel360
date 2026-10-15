// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Ops Alert"] = {
	add_fields: ["severity", "status", "is_outage", "occurrence_count"],
	filters: [["status", "in", ["Open", "Acknowledged"]]],
	hide_name_column: true,
	get_indicator(doc) {
		if (doc.status === "Resolved") {
			return [__("Resolved"), "green", "status,=,Resolved"];
		}
		if (doc.status === "Suppressed") {
			return [__("Suppressed"), "gray", "status,=,Suppressed"];
		}
		if (doc.status === "Acknowledged") {
			return [__("Acknowledged"), "yellow", "status,=,Acknowledged"];
		}
		const colour = { Critical: "red", Warning: "orange", Info: "blue" }[doc.severity] || "orange";
		const label = doc.is_outage ? __("Outage") : __(doc.severity);
		return [label, colour, `status,=,Open|severity,=,${doc.severity}`];
	},
	formatters: {
		occurrence_count(value) {
			return value > 1 ? `×${value}` : "";
		},
	},
};
