// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Ops Backup Record"] = {
	add_fields: ["status", "backup_type"],
	get_indicator(doc) {
		return doc.status === "Success"
			? [__("Success"), "green", "status,=,Success"]
			: [__("Failed"), "red", "status,=,Failed"];
	},
};
