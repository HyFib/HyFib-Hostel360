// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Uptime and Incidents"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "uptime_percent" && data && data.uptime_percent !== null && data.uptime_percent !== undefined) {
			let css = "text-danger";
			if (data.uptime_percent >= 99.9) {
				css = "text-success";
			} else if (data.uptime_percent >= 99) {
				css = "text-warning";
			}
			value = `<span class="${css}">${value}</span>`;
		}
		return value;
	},
};
