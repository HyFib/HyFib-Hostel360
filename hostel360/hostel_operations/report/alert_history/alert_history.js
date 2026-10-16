// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Alert History"] = {
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
		{
			fieldname: "severity",
			label: __("Severity"),
			fieldtype: "Select",
			options: ["", "Info", "Warning", "Critical"],
		},
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Select",
			options: [
				"",
				"Availability",
				"Performance",
				"Capacity",
				"Backup",
				"Security",
				"Payments",
				"Messaging",
				"Jobs",
				"Errors",
				"SLA",
				"Other",
			],
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", "Open", "Acknowledged", "Resolved", "Suppressed"],
		},
		{
			fieldname: "source",
			label: __("Source"),
			fieldtype: "Select",
			options: ["", "App", "Health Check", "Host Agent", "OCI Monitoring", "Uptime Probe", "Security", "Support"],
		},
		{
			fieldname: "outages_only",
			label: __("Outages Only"),
			fieldtype: "Check",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "severity" && data) {
			const colour = { Critical: "red", Warning: "orange", Info: "blue" }[data.severity];
			if (colour) {
				value = `<span class="indicator-pill ${colour}">${value}</span>`;
			}
		}
		return value;
	},
};
