// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Room Allotment History"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{ fieldname: "hostel", label: __("Hostel"), fieldtype: "Link", options: "Hostel" },
		{
			fieldname: "room",
			label: __("Room"),
			fieldtype: "Link",
			options: "Hostel Room",
			get_query: () => ({
				filters: frappe.query_report.get_filter_value("hostel")
					? { hostel: frappe.query_report.get_filter_value("hostel") }
					: {},
			}),
		},
		{ fieldname: "student", label: __("Student"), fieldtype: "Link", options: "Hostel Student" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nActive\nTransferred\nVacated\nCancelled",
		},
		{
			fieldname: "allotment_type",
			label: __("Type"),
			fieldtype: "Select",
			options: "\nNew\nTransfer",
		},
		{ fieldname: "academic_year", label: __("Academic Year"), fieldtype: "Link", options: "Hostel Academic Year" },
	],

	onload(report) {
		// Opened from a student or room form: show the whole history, not just the last year.
		if (report.get_filter_value("student") || report.get_filter_value("room")) {
			report.set_filter_value("from_date", "");
		}
	},

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const colour = { Active: "green", Transferred: "blue", Vacated: "gray", Cancelled: "red" }[data.status];
			if (colour) value = `<span class="indicator-pill ${colour}">${value}</span>`;
		}
		return value;
	},
};
