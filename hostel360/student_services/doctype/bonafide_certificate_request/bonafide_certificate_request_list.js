// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Bonafide Certificate Request"] = {
	add_fields: ["status", "valid_till"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		if (["Approved", "Issued"].includes(doc.status) && doc.valid_till && doc.valid_till < frappe.datetime.get_today()) {
			return [__("Expired"), "gray", "valid_till,<," + frappe.datetime.get_today()];
		}
		const colours = { Pending: "orange", Approved: "green", Issued: "blue", Rejected: "red" };
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
};
