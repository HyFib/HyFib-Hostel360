// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Leave Register"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{ fieldname: "hostel", label: __("Hostel"), fieldtype: "Link", options: "Hostel" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				"",
				"Pending Parent Approval",
				"Pending Warden Approval",
				"Approved",
				"Rejected",
				"Cancelled",
				"Checked Out",
				"Returned",
				"Overdue",
			],
		},
		{ fieldname: "student", label: __("Student"), fieldtype: "Link", options: "Hostel Student" },
		{
			fieldname: "applied_by",
			label: __("Applied By"),
			fieldtype: "Select",
			options: ["", "Student", "Parent", "Warden"],
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const colour = { Overdue: "red", Rejected: "red", Approved: "green", "Checked Out": "blue" }[data.status];
			if (colour) value = `<span class="indicator-pill ${colour}">${value}</span>`;
		}
		if (column.fieldname === "late_by_hours" && data && data.late_by_hours) {
			value = `<span class="text-danger">${value}</span>`;
		}
		return value;
	},
};
