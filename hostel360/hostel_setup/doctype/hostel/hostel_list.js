frappe.listview_settings["Hostel"] = {
	add_fields: ["is_active", "vacant_beds", "total_capacity"],
	get_indicator(doc) {
		if (!doc.is_active) return [__("Inactive"), "gray", "is_active,=,0"];
		if (doc.total_capacity && !doc.vacant_beds) return [__("Full"), "red", "vacant_beds,=,0"];
		return [__("Active"), "green", "is_active,=,1"];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Room Board"), () => frappe.set_route("room-board"));
	},
};
