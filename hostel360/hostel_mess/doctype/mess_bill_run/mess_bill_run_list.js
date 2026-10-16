// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Mess Bill Run"] = {
	add_fields: ["status", "docstatus"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		const colours = { Draft: "red", Computed: "orange", Published: "green", Cancelled: "darkgrey" };
		return [__(doc.status), colours[doc.status] || "grey", "status,=," + doc.status];
	},
};
