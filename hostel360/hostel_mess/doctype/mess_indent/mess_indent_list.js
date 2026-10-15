// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Mess Indent"] = {
	add_fields: ["status", "docstatus"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		const colours = {
			Draft: "red",
			"Pending Approval": "orange",
			Approved: "blue",
			Rejected: "red",
			Ordered: "purple",
			Received: "green",
			Cancelled: "darkgrey",
		};
		return [__(doc.status), colours[doc.status] || "grey", "status,=," + doc.status];
	},
};
