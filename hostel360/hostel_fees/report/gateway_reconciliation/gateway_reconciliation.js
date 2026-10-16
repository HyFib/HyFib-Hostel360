// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Gateway Reconciliation"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -7),
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
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", "Initiated", "Pending", "Success", "Failed", "Expired", "Refunded"],
		},
		{ fieldname: "hostel", label: __("Hostel"), fieldtype: "Link", options: "Hostel" },
		{ fieldname: "only_issues", label: __("Only Needing Attention"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "issue" && data && data.issue) {
			value = `<span style="color: var(--red-600); font-weight: 600;">${value}</span>`;
		}
		return value;
	},
};
