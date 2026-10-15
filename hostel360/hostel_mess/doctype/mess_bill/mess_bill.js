// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mess Bill", {
	refresh(frm) {
		frm.disable_save();
		const invoice = frm.doc.__onload?.invoice;
		if (invoice) {
			const colours = { Paid: "green", Unpaid: "orange", "Partly Paid": "yellow", Overdue: "red", Cancelled: "darkgrey" };
			frm.dashboard.add_indicator(__("Invoice: {0}", [__(invoice.status)]), colours[invoice.status] || "grey");
			if (flt(invoice.outstanding_amount)) {
				frm.dashboard.add_indicator(__("Outstanding: {0}", [format_currency(invoice.outstanding_amount)]), "red");
			}
		}
		if (frm.doc.fee_invoice) {
			frm.add_custom_button(__("Fee Invoice"), () => frappe.set_route("Form", "Hostel Fee Invoice", frm.doc.fee_invoice));
		}
		frm.add_custom_button(__("Bill Run"), () => frappe.set_route("Form", "Mess Bill Run", frm.doc.mess_bill_run));
	},
});
