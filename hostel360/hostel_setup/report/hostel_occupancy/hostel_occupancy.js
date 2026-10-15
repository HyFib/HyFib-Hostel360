// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Hostel Occupancy"] = {
	filters: [
		{
			fieldname: "hostel",
			label: __("Hostel"),
			fieldtype: "Link",
			options: "Hostel",
			get_query: () => ({ filters: { is_active: 1 } }),
			on_change: () => {
				frappe.query_report.set_filter_value("block", "");
			},
		},
		{
			fieldname: "block",
			label: __("Block"),
			fieldtype: "Link",
			options: "Hostel Block",
			get_query: () => ({
				filters: { hostel: frappe.query_report.get_filter_value("hostel") || "" },
			}),
		},
		{
			fieldname: "room_type",
			label: __("Room Type"),
			fieldtype: "Select",
			options: "\nSingle\nDouble\nTriple\nFour Sharing\nDormitory",
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: "Hostel\nBlock\nFloor\nRoom",
			default: "Block",
			reqd: 1,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "occupancy_percent" && data) {
			const colour = data.occupancy_percent >= 95 ? "red" : data.occupancy_percent >= 75 ? "orange" : "green";
			value = `<span style="color: var(--${colour}-600)">${value}</span>`;
		}
		return value;
	},

	onload(report) {
		report.page.add_inner_button(__("Room Board"), () =>
			frappe.set_route("room-board", {
				hostel: report.get_filter_value("hostel") || undefined,
			})
		);
	},
};
