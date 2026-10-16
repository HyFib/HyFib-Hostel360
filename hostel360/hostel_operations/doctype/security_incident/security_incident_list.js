// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Security Incident"] = {
	add_fields: ["status", "reporting_sla_breached"],
	get_indicator(doc) {
		if (doc.status === "Open") {
			return doc.reporting_sla_breached
				? [__("Report Overdue"), "red", "status,=,Open"]
				: [__("Open"), "orange", "status,=,Open"];
		}
		const colours = { Reported: "blue", Closed: "green", "Not Reportable": "gray" };
		return [__(doc.status), colours[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
