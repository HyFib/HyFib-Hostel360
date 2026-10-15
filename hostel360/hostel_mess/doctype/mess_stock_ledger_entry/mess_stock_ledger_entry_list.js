// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Mess Stock Ledger Entry"] = {
	add_fields: ["is_cancelled", "actual_qty"],
	get_indicator(doc) {
		if (doc.is_cancelled) return [__("Cancelled"), "darkgrey", "is_cancelled,=,1"];
		return flt(doc.actual_qty) >= 0
			? [__("In"), "green", "actual_qty,>=,0"]
			: [__("Out"), "blue", "actual_qty,<,0"];
	},
};
