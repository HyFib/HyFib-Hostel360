// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Support Ticket"] = {
	add_fields: ["status", "priority", "response_sla_breached", "resolution_sla_breached"],
	get_indicator(doc) {
		if (doc.status === "Closed") {
			return [__("Closed"), "gray", "status,=,Closed"];
		}
		if (doc.status === "Resolved") {
			return [__("Resolved"), "green", "status,=,Resolved"];
		}
		if (doc.response_sla_breached || doc.resolution_sla_breached) {
			return [__("SLA Breached"), "red", "resolution_sla_breached,=,1"];
		}
		const colours = {
			Open: "orange",
			Acknowledged: "yellow",
			"In Progress": "blue",
			"Waiting on Institution": "purple",
		};
		return [__(doc.status), colours[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
