// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

(() => {
	frappe.ui.form.on("Hostel Payment", {
		setup(frm) {
			frm.set_query("invoice", "allocations", () => ({
				filters: {
					student: frm.doc.student,
					docstatus: 1,
					outstanding_amount: [">", 0],
				},
			}));
		},

		refresh(frm) {
			if (frm.doc.docstatus === 0 && frm.doc.student) {
				frm.add_custom_button(__("Get Outstanding Invoices"), () => fetch_outstanding(frm));
			}
			if (frm.doc.docstatus === 1) {
				frm.add_custom_button(__("Print Receipt"), () => frm.print_doc()).addClass("btn-primary");
			}
			if (frm.doc.payment_transaction) {
				frm.add_custom_button(
					__("Online Transaction"),
					() => frappe.set_route("Form", "Hostel Payment Transaction", frm.doc.payment_transaction),
					__("View")
				);
			}
			if (frm.doc.mode_of_payment === "Online" && frm.doc.docstatus === 0 && !frm.doc.payment_transaction) {
				frm.dashboard.set_headline(
					__("Online receipts are issued automatically by the payment gateway. Choose the mode the money was received in."),
					"orange"
				);
			}
		},

		student(frm) {
			frm.clear_table("allocations");
			frm.refresh_field("allocations");
			set_payment_totals(frm);
			if (frm.doc.student) fetch_outstanding(frm);
		},
	});

	frappe.ui.form.on("Hostel Payment Allocation", {
		allocated_amount: (frm) => set_payment_totals(frm),
		allocations_remove: (frm) => set_payment_totals(frm),
	});

	function fetch_outstanding(frm) {
		frappe
			.call({
				method: "hostel360.hostel_fees.desk.get_outstanding_invoices",
				args: { student: frm.doc.student },
			})
			.then((r) => {
				const dues = r.message || [];
				if (!dues.length) {
					frappe.show_alert({ message: __("No outstanding invoices for this student"), indicator: "orange" });
					return;
				}
				frm.clear_table("allocations");
				dues.forEach((invoice) => {
					const row = frm.add_child("allocations");
					row.invoice = invoice.name;
					row.invoice_type = invoice.invoice_type;
					row.due_date = invoice.due_date;
					row.outstanding_before = invoice.outstanding_amount;
					row.allocated_amount = invoice.outstanding_amount;
				});
				frm.refresh_field("allocations");
				set_payment_totals(frm, true);
			});
	}

	function set_payment_totals(frm, reset_amount = false) {
		const allocated = (frm.doc.allocations || []).reduce((sum, row) => sum + flt(row.allocated_amount), 0);
		const previous = flt(frm.doc.total_allocated);
		frm.set_value("total_allocated", allocated);
		// Keep the amount in step with the allocation unless the cashier typed a different figure.
		if (reset_amount || !flt(frm.doc.amount) || flt(frm.doc.amount) === previous) {
			frm.set_value("amount", allocated);
		}
		frm.set_value("unallocated_amount", flt(frm.doc.amount) - allocated);
	}
})();
