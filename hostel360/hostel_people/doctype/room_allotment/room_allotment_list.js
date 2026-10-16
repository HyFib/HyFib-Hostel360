frappe.listview_settings["Room Allotment"] = {
	add_fields: ["status", "allotment_type", "docstatus"],
	get_indicator(doc) {
		if (doc.docstatus === 0) return [__("Draft"), "red", "docstatus,=,0"];
		const colours = {
			Active: "green",
			Transferred: "blue",
			Vacated: "gray",
			Cancelled: "red",
		};
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Room Board"), () => frappe.set_route("room-board"));
	},
};
