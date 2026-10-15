// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

(() => {
	frappe.listview_settings["Hostel Payment"] = {
		add_fields: ["mode_of_payment", "amount", "docstatus"],

		get_indicator(doc) {
			if (doc.docstatus === 2) return [__("Cancelled"), "red", "docstatus,=,2"];
			if (doc.docstatus === 0) return [__("Draft"), "gray", "docstatus,=,0"];
			const color = doc.mode_of_payment === "Online" ? "blue" : "green";
			return [__(doc.mode_of_payment), color, `mode_of_payment,=,${doc.mode_of_payment}`];
		},
	};
})();
