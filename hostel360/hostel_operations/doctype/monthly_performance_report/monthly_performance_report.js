// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Monthly Performance Report", {
	refresh(frm) {
		if (frm.is_new() || !frappe.user.has_role(["HyFib Operations", "System Manager"])) {
			return;
		}
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Refresh Figures"), () => {
				frm.call({ method: "refresh_figures", doc: frm.doc, freeze: true }).then(() => frm.reload_doc());
			});
		}
		if (frm.doc.docstatus === 1) {
			const label = frm.doc.sent_on ? __("Send Again") : __("Send to Recipients");
			frm.add_custom_button(label, () => {
				frappe.confirm(__("E-mail this report as a PDF to the institution officials and the HyFib helpdesk?"), () => {
					frappe
						.call({
							method: "hostel360.hostel_operations.reports.send_report_now",
							args: { name: frm.doc.name },
							freeze: true,
						})
						.then((r) => {
							const result = r.message || {};
							if (result.sent) {
								frappe.show_alert({
									message: __("Sent to {0}", [result.recipients.join(", ")]),
									indicator: "green",
								});
							} else {
								frappe.msgprint(__("Add Report Recipients in Ops Alert Settings first."));
							}
							frm.reload_doc();
						});
				});
			});
		}
	},
});
