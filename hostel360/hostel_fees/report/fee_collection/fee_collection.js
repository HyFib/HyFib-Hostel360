// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Fee Collection"] = {
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
			fieldname: "mode_of_payment",
			label: __("Mode of Payment"),
			fieldtype: "Select",
			options: ["", "Online", "Cash", "UPI", "Demand Draft", "Cheque", "Bank Transfer"],
		},
		{ fieldname: "student", label: __("Student"), fieldtype: "Link", options: "Hostel Student" },
	],
};
