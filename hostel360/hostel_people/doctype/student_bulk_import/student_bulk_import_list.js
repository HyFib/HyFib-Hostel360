frappe.listview_settings["Student Bulk Import"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const colours = {
			Draft: "gray",
			Validated: "blue",
			Importing: "orange",
			Completed: "green",
			"Completed with Errors": "yellow",
			Failed: "red",
		};
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
};
