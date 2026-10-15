// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Student Register"] = {
	filters: [
		{ fieldname: "hostel", label: __("Hostel"), fieldtype: "Link", options: "Hostel" },
		{ fieldname: "program", label: __("Program"), fieldtype: "Link", options: "Academic Program" },
		{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Academic Department" },
		{ fieldname: "year_of_study", label: __("Year"), fieldtype: "Select", options: "\n1\n2\n3\n4\n5" },
		{ fieldname: "gender", label: __("Gender"), fieldtype: "Select", options: "\nMale\nFemale\nOther" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nApplied\nActive\nSuspended\nVacated\nAlumni",
			default: "Active",
		},
		{
			fieldname: "validation_status",
			label: __("Validation"),
			fieldtype: "Select",
			options: "\nPending\nValidated\nRejected",
		},
		{
			fieldname: "allotted",
			label: __("Room"),
			fieldtype: "Select",
			options: "\nAllotted\nNot Allotted",
		},
		{ fieldname: "academic_year", label: __("Academic Year"), fieldtype: "Link", options: "Hostel Academic Year" },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "validation_status" && data) {
			const colour = { Validated: "green", Pending: "orange", Rejected: "red" }[data.validation_status];
			if (colour) value = `<span class="indicator-pill ${colour}">${value}</span>`;
		}
		return value;
	},
};
