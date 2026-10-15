// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Mess Extra Charge"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		return doc.status === "Billed"
			? [__("Billed"), "green", "status,=,Billed"]
			: [__("Unbilled"), "orange", "status,=,Unbilled"];
	},
};
