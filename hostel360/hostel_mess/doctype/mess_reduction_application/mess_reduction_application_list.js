// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Mess Reduction Application"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const colours = { Pending: "orange", Approved: "green", Rejected: "red", Cancelled: "darkgrey" };
		return [__(doc.status), colours[doc.status] || "grey", "status,=," + doc.status];
	},
};
