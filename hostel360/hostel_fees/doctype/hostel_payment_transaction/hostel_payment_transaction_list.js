// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

(() => {
	frappe.listview_settings["Hostel Payment Transaction"] = {
		add_fields: ["status", "amount", "test_mode"],

		get_indicator(doc) {
			const colors = {
				Initiated: "blue",
				Pending: "orange",
				Success: "green",
				Failed: "red",
				Expired: "gray",
				Refunded: "purple",
			};
			return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
		},
	};
})();
