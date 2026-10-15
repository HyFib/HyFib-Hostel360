// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Mess Reduction Register"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
		},
		{ fieldname: "mess", label: __("Mess"), fieldtype: "Link", options: "Hostel Mess" },
		{ fieldname: "hostel", label: __("Hostel"), fieldtype: "Link", options: "Hostel" },
		{ fieldname: "student", label: __("Student"), fieldtype: "Link", options: "Hostel Student" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", "Pending", "Approved", "Rejected", "Cancelled"],
		},
		{
			fieldname: "reason",
			label: __("Reason"),
			fieldtype: "Select",
			options: ["", "Home Visit", "Medical", "Internship", "Sports or Cultural Event", "College Tour", "Other"],
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const colours = { Pending: "orange", Approved: "green", Rejected: "red", Cancelled: "gray" };
			value = `<span class="indicator-pill ${colours[data.status] || "gray"}">${value}</span>`;
		}
		return value;
	},
};
