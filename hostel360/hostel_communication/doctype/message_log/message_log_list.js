// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Message Log"] = {
	add_fields: ["status", "channel", "attempts"],
	get_indicator(doc) {
		const colours = { Sent: "green", Queued: "blue", Failed: "red", Skipped: "gray" };
		return [__(doc.status), colours[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
