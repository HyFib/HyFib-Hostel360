// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Grievance Summary"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.month_start(), -2),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Category", "Hostel"],
			default: "Category",
		},
		{ fieldname: "hostel", label: __("Hostel"), fieldtype: "Link", options: "Hostel" },
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Select",
			options: [
				"",
				"Room and Furniture",
				"Electrical",
				"Plumbing",
				"Cleanliness",
				"Mess and Food",
				"Water",
				"Internet",
				"Security",
				"Health",
				"Ragging or Harassment",
				"Staff Behaviour",
				"Other",
			],
		},
		{
			fieldname: "priority",
			label: __("Priority"),
			fieldtype: "Select",
			options: ["", "Low", "Medium", "High", "Urgent"],
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (["escalated", "sla_breaches"].includes(column.fieldname) && data && data[column.fieldname] > 0) {
			value = `<span class="text-danger bold">${value}</span>`;
		}
		return value;
	},
};
