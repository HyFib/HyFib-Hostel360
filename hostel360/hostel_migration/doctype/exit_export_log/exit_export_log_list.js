// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Exit Export Log"] = {
	add_fields: ["status", "purged_on"],
	get_indicator(doc) {
		if (doc.purged_on) {
			return [__("Data Deleted"), "purple", "purged_on,is,set"];
		}
		const colours = { Exported: "blue", Verified: "green", "Verification Failed": "red" };
		return [__(doc.status), colours[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
