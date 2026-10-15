// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Bonafide Register"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
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
			options: ["", "Pending", "Approved", "Rejected", "Issued"],
		},
		{
			fieldname: "purpose",
			label: __("Purpose"),
			fieldtype: "Select",
			options: ["", "Bank Loan", "Scholarship", "Passport", "Visa", "Internship", "Address Proof", "Other"],
		},
		{ fieldname: "student", label: __("Student"), fieldtype: "Link", options: "Hostel Student" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "validity" && data && data.validity) {
			const colour = data.validity === __("Valid") ? "green" : "gray";
			value = `<span class="indicator-pill ${colour}">${value}</span>`;
		}
		return value;
	},
};
