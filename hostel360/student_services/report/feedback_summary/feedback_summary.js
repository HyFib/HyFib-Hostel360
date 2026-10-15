// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Feedback Summary"] = {
	filters: [
		{
			fieldname: "from_period",
			label: __("From Period"),
			fieldtype: "Data",
			description: __("YYYY-MM"),
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -5).slice(0, 7),
			reqd: 1,
		},
		{
			fieldname: "to_period",
			label: __("To Period"),
			fieldtype: "Data",
			description: __("YYYY-MM"),
			default: frappe.datetime.get_today().slice(0, 7),
			reqd: 1,
		},
		{ fieldname: "hostel", label: __("Hostel"), fieldtype: "Link", options: "Hostel" },
		{
			fieldname: "feedback_type",
			label: __("Feedback Type"),
			fieldtype: "Select",
			options: ["", "Mess", "Hostel", "Facilities", "General"],
		},
		{ fieldname: "show_comments", label: __("Show Individual Feedback"), fieldtype: "Check", default: 0 },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		const rating = data && data[column.fieldname];
		if (column.fieldtype === "Float" && typeof rating === "number" && rating > 0 && rating < 3) {
			value = `<span class="text-danger">${value}</span>`;
		}
		return value;
	},
};
