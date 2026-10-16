// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Mess Cost Summary"] = {
	filters: [
		{
			fieldname: "mess",
			label: __("Mess"),
			fieldtype: "Link",
			options: "Hostel Mess",
			reqd: 1,
			get_query: () => ({ filters: { is_active: 1 } }),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.month_start(), -11),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
	],

	onload(report) {
		if (report.get_filter_value("mess")) return;
		frappe.db
			.get_list("Hostel Mess", { filters: { is_active: 1 }, fields: ["name", "mess_manager"], limit: 20 })
			.then((rows) => {
				const choice = rows.find((row) => row.mess_manager === frappe.session.user) || (rows.length === 1 && rows[0]);
				if (choice) report.set_filter_value("mess", choice.name);
			});
	},
};
