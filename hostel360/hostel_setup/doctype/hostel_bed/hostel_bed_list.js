frappe.listview_settings["Hostel Bed"] = {
	add_fields: ["status", "is_active", "current_student_name"],
	filters: [["is_active", "=", 1]],
	get_indicator(doc) {
		if (!doc.is_active) return [__("Inactive"), "gray", "is_active,=,0"];
		const colours = {
			Vacant: "green",
			Occupied: "blue",
			Reserved: "orange",
			Maintenance: "gray",
		};
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Room Board"), () => frappe.set_route("room-board"));
	},
};
