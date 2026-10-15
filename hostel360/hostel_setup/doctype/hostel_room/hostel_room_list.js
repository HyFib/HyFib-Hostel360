frappe.listview_settings["Hostel Room"] = {
	add_fields: ["status", "is_active", "occupied_beds", "capacity"],
	get_indicator(doc) {
		if (!doc.is_active) return [__("Inactive"), "gray", "is_active,=,0"];
		const colours = {
			Available: "green",
			"Partially Occupied": "blue",
			Full: "red",
			"Under Maintenance": "orange",
			Blocked: "darkgrey",
		};
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
	formatters: {
		capacity(value, df, doc) {
			return `${doc.occupied_beds || 0} / ${value || 0}`;
		},
	},
	onload(listview) {
		listview.page.add_inner_button(__("Room Board"), () => frappe.set_route("room-board"));
	},
};
