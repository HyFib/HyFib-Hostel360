frappe.listview_settings["Hostel Guardian"] = {
	add_fields: ["validation_status"],
	get_indicator(doc) {
		const colours = { Pending: "orange", Validated: "green", Rejected: "red" };
		return [
			__(doc.validation_status),
			colours[doc.validation_status] || "gray",
			"validation_status,=," + doc.validation_status,
		];
	},
};
