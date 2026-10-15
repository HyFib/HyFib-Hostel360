// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

(() => {
	const TRANSACTION_INDICATORS = {
		Initiated: "blue",
		Pending: "orange",
		Success: "green",
		Failed: "red",
		Expired: "gray",
		Refunded: "purple",
	};

	frappe.ui.form.on("Hostel Payment Transaction", {
		refresh(frm) {
			frm.disable_save();
			frm.page.set_indicator(__(frm.doc.status), TRANSACTION_INDICATORS[frm.doc.status] || "gray");

			if (frm.doc.test_mode) {
				frm.dashboard.set_headline(__("Test mode transaction: no real money was collected."), "blue");
			}

			const staff = frappe.user.has_role(["Hostel Administrator", "Hostel Office Staff", "System Manager"]);
			if (["Initiated", "Pending"].includes(frm.doc.status) && staff) {
				frm.add_custom_button(__("Check with Gateway"), () =>
					frm.call({ doc: frm.doc, method: "check_with_gateway", freeze: true }).then((r) => {
						const result = r.message || {};
						frappe.show_alert({
							message: result.message || __("Status: {0}", [__(result.status)]),
							indicator: TRANSACTION_INDICATORS[result.status] || "blue",
						});
						frm.reload_doc();
					})
				);
			}

			if (frm.doc.payment) {
				frm.add_custom_button(
					__("Receipt"),
					() => frappe.set_route("Form", "Hostel Payment", frm.doc.payment),
					__("View")
				);
			}

			const admin = frappe.user.has_role(["Hostel Administrator", "System Manager"]);
			if (frm.doc.status === "Success" && admin) {
				frm.add_custom_button(
					__("Mark as Refunded"),
					() =>
						frappe.prompt(
							{
								fieldname: "remarks",
								fieldtype: "Small Text",
								label: __("Refund reference and reason"),
								reqd: 1,
							},
							(values) =>
								frm
									.call({ doc: frm.doc, method: "mark_refunded", args: values, freeze: true })
									.then(() => frm.reload_doc()),
							__("Mark as Refunded"),
							__("Mark Refunded")
						),
					__("Actions")
				);
			}
		},
	});
})();
