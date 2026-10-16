// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Mess Bill"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const colours = { Draft: "orange", Published: "green", Cancelled: "darkgrey" };
		return [__(doc.status), colours[doc.status] || "grey", "status,=," + doc.status];
	},
};
