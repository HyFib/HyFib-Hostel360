// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Students On Leave"] = {
	filters: [
		{
			fieldname: "as_of",
			label: __("As Of"),
			fieldtype: "Datetime",
			default: frappe.datetime.now_datetime(),
			reqd: 1,
		},
		{ fieldname: "hostel", label: __("Hostel"), fieldtype: "Link", options: "Hostel" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "movement" && data && data.overdue_hours) {
			value = `<span class="indicator-pill red">${value}</span>`;
		}
		return value;
	},
};
