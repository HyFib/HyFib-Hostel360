// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Mess Stock Balance"] = {
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
			fieldname: "as_on_date",
			label: __("As On"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Select",
			options: [
				"",
				"Grains",
				"Pulses",
				"Vegetables",
				"Fruits",
				"Dairy",
				"Meat and Eggs",
				"Oil and Ghee",
				"Spices",
				"Fuel",
				"Cleaning",
				"Other",
			],
		},
		{ fieldname: "below_reorder_only", label: __("Only Items to Reorder"), fieldtype: "Check" },
		{ fieldname: "show_zero_stock", label: __("Include Items Without Stock"), fieldtype: "Check" },
	],

	onload(report) {
		// Pre-select the mess the user manages, or the only active mess.
		if (report.get_filter_value("mess")) return;
		frappe.db
			.get_list("Hostel Mess", { filters: { is_active: 1 }, fields: ["name", "mess_manager"], limit: 20 })
			.then((rows) => {
				const choice = rows.find((row) => row.mess_manager === frappe.session.user) || (rows.length === 1 && rows[0]);
				if (choice) report.set_filter_value("mess", choice.name);
			});
	},

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "stock_status" && data) {
			const low = flt(data.reorder_level) > 0 && flt(data.qty) <= flt(data.reorder_level);
			value = `<span class="indicator-pill ${low ? "red" : "green"}">${value}</span>`;
		}
		return value;
	},
};
