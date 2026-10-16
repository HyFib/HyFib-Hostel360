// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Migration Run"] = {
	add_fields: ["status", "dry_run", "reconciliation_status", "total_failed"],
	get_indicator(doc) {
		const prefix = doc.dry_run ? __("Dry Run") + ": " : "";
		const colours = {
			Draft: "gray",
			Running: "blue",
			Completed: "green",
			"Completed with Errors": "orange",
			Failed: "red",
		};
		return [prefix + __(doc.status), colours[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
