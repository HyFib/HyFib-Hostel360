frappe.listview_settings["Hostel Student"] = {
	add_fields: ["status", "validation_status", "current_bed", "photo"],
	get_indicator(doc) {
		if (doc.validation_status === "Rejected") {
			return [__("Rejected"), "red", "validation_status,=,Rejected"];
		}
		if (doc.validation_status === "Pending") {
			return [__("Pending Validation"), "orange", "validation_status,=,Pending"];
		}
		const colours = {
			Applied: "blue",
			Active: "green",
			Suspended: "red",
			Vacated: "gray",
			Alumni: "darkgrey",
		};
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Bulk Import"), () =>
			frappe.new_doc("Student Bulk Import")
		);
		listview.page.add_inner_button(__("Room Board"), () => frappe.set_route("room-board"));
	},
};
