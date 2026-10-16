// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Fee Dues"] = {
	filters: [
		{
			fieldname: "as_on_date",
			label: __("As On"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{ fieldname: "hostel", label: __("Hostel"), fieldtype: "Link", options: "Hostel" },
		{
			fieldname: "invoice_type",
			label: __("Invoice Type"),
			fieldtype: "Select",
			options: ["", "Hostel Fee", "Mess Bill", "Fine", "Other"],
		},
		{
			fieldname: "academic_year",
			label: __("Academic Year"),
			fieldtype: "Link",
			options: "Hostel Academic Year",
		},
		{ fieldname: "student", label: __("Student"), fieldtype: "Link", options: "Hostel Student" },
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Invoice", "Student"],
			default: "Invoice",
		},
		{ fieldname: "overdue_only", label: __("Overdue Only"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "days_past_due" && data && data.days_past_due > 60) {
			value = `<span style="color: var(--red-600); font-weight: 600;">${value}</span>`;
		}
		return value;
	},
};
