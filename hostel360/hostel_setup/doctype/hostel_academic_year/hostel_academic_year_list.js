frappe.listview_settings["Hostel Academic Year"] = {
	add_fields: ["is_current"],
	get_indicator(doc) {
		return doc.is_current
			? [__("Current"), "green", "is_current,=,1"]
			: [__("Not Current"), "gray", "is_current,=,0"];
	},
};
