frappe.listview_settings["Hostel Staff"] = {
	add_fields: ["is_active", "user"],
	get_indicator(doc) {
		if (!doc.is_active) return [__("Inactive"), "gray", "is_active,=,0"];
		return doc.user
			? [__("Active · Desk Login"), "green", "is_active,=,1"]
			: [__("Active"), "blue", "is_active,=,1"];
	},
};
