// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Monthly Performance Report"] = {
	add_fields: ["sent_on", "docstatus"],
	get_indicator(doc) {
		if (doc.docstatus === 2) {
			return [__("Cancelled"), "red", "docstatus,=,2"];
		}
		if (doc.docstatus === 0) {
			return [__("Draft"), "gray", "docstatus,=,0"];
		}
		return doc.sent_on ? [__("Sent"), "green", "sent_on,is,set"] : [__("Not Sent"), "orange", "sent_on,is,not set"];
	},
	onload(listview) {
		if (!frappe.user.has_role(["HyFib Operations", "System Manager"])) {
			return;
		}
		listview.page.add_inner_button(__("Generate for a Month"), () => {
			const last = frappe.datetime.add_months(frappe.datetime.month_start(), -1);
			frappe.prompt(
				{
					fieldname: "month",
					fieldtype: "Data",
					label: __("Month (YYYY-MM)"),
					default: last.substring(0, 7),
					reqd: 1,
				},
				(values) => {
					frappe
						.call({
							method: "hostel360.hostel_operations.reports.generate_report",
							args: { month: values.month },
							freeze: true,
							freeze_message: __("Collecting figures..."),
						})
						.then((r) => frappe.set_route("Form", "Monthly Performance Report", r.message));
				},
				__("Generate Monthly Report"),
				__("Generate")
			);
		});
	},
};
