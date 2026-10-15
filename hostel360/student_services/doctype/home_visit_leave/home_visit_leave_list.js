// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Home Visit Leave"] = {
	add_fields: ["status", "from_datetime", "to_datetime", "hostel"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		const colours = {
			"Pending Parent Approval": "orange",
			"Pending Warden Approval": "yellow",
			Approved: "green",
			Rejected: "red",
			Cancelled: "gray",
			"Checked Out": "blue",
			Returned: "darkgrey",
			Overdue: "red",
		};
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
};
