// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Hostel Feedback"] = {
	add_fields: ["overall", "is_anonymous", "action_taken"],
	get_indicator(doc) {
		const stars = Math.round((doc.overall || 0) * 5);
		const colour = stars >= 4 ? "green" : stars === 3 ? "yellow" : "red";
		return [__("{0} of 5", [stars]), colour, "overall,=," + doc.overall];
	},
};
