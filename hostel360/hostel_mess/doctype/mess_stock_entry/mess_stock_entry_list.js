// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Mess Stock Entry"] = {
	add_fields: ["entry_type", "docstatus"],
	get_indicator(doc) {
		if (doc.docstatus === 0) return [__("Draft"), "red", "docstatus,=,0"];
		if (doc.docstatus === 2) return [__("Cancelled"), "darkgrey", "docstatus,=,2"];
		const colours = {
			Receipt: "green",
			Issue: "blue",
			Adjustment: "orange",
			"Return to Supplier": "purple",
		};
		return [__(doc.entry_type), colours[doc.entry_type] || "grey", "entry_type,=," + doc.entry_type];
	},
};
