// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Mess Menu"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		return doc.status === "Published"
			? [__("Published"), "green", "status,=,Published"]
			: [__("Draft"), "orange", "status,=,Draft"];
	},
};
