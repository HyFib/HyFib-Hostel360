// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

(() => {
	// Fee counter dialog: pick a student, see open invoices, record what was received.
	// Shared by the invoice form ("Record Payment") and the invoice list ("Collect Fees").
	frappe.provide("hostel360.fees");

	hostel360.fees.collect_fees = function ({ student = null, invoice = null, on_success = null } = {}) {
		const OFFLINE_MODES = ["Cash", "UPI", "Demand Draft", "Cheque", "Bank Transfer"];
		const fmt = (value) => format_currency(value || 0, "INR");

		const dialog = new frappe.ui.Dialog({
			title: __("Collect Fees"),
			size: "large",
			fields: [
				{
					fieldname: "student",
					fieldtype: "Link",
					label: __("Student"),
					options: "Hostel Student",
					reqd: 1,
					default: student,
					read_only: student ? 1 : 0,
					onchange: () => load_dues(),
				},
				{ fieldname: "student_info", fieldtype: "HTML" },
				{ fieldtype: "Section Break", label: __("Open Invoices") },
				{
					fieldname: "dues",
					fieldtype: "Table",
					cannot_add_rows: true,
					cannot_delete_rows: true,
					in_place_edit: true,
					data: [],
					fields: [
						{
							fieldname: "invoice",
							fieldtype: "Link",
							options: "Hostel Fee Invoice",
							label: __("Invoice"),
							read_only: 1,
							in_list_view: 1,
							columns: 3,
						},
						{
							fieldname: "invoice_type",
							fieldtype: "Data",
							label: __("Type"),
							read_only: 1,
							in_list_view: 1,
							columns: 2,
						},
						{
							fieldname: "due_date",
							fieldtype: "Date",
							label: __("Due"),
							read_only: 1,
							in_list_view: 1,
							columns: 2,
						},
						{
							fieldname: "outstanding_amount",
							fieldtype: "Currency",
							label: __("Outstanding"),
							read_only: 1,
							in_list_view: 1,
							columns: 1,
						},
						{
							fieldname: "allocated_amount",
							fieldtype: "Currency",
							label: __("Receiving"),
							in_list_view: 1,
							columns: 2,
							onchange: () => update_total(),
						},
					],
				},
				{ fieldname: "total_html", fieldtype: "HTML" },
				{ fieldtype: "Section Break", label: __("Payment") },
				{
					fieldname: "mode_of_payment",
					fieldtype: "Select",
					label: __("Mode of Payment"),
					options: OFFLINE_MODES.join("\n"),
					default: "Cash",
					reqd: 1,
					onchange: () => toggle_reference(),
				},
				{
					fieldname: "payment_date",
					fieldtype: "Date",
					label: __("Payment Date"),
					default: frappe.datetime.get_today(),
					reqd: 1,
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "reference_no",
					fieldtype: "Data",
					label: __("Reference No"),
					description: __("UPI/UTR, DD or cheque number"),
				},
				{ fieldname: "reference_date", fieldtype: "Date", label: __("Reference Date") },
				{ fieldname: "bank_name", fieldtype: "Data", label: __("Bank / Branch") },
				{ fieldtype: "Section Break" },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			],
			primary_action_label: __("Record Payment"),
			primary_action: (values) => submit(values),
		});

		function rows() {
			return dialog.fields_dict.dues.df.data || [];
		}

		function total() {
			return rows().reduce((sum, row) => sum + flt(row.allocated_amount), 0);
		}

		function update_total() {
			dialog.fields_dict.total_html.$wrapper.html(
				`<div class="text-right" style="font-size: var(--text-lg); padding: var(--padding-sm) 0;">
					${__("Total receiving")}: <strong>${fmt(total())}</strong>
				</div>`
			);
		}

		function toggle_reference() {
			const mode = dialog.get_value("mode_of_payment");
			const needs_reference = mode && mode !== "Cash";
			dialog.set_df_property("reference_no", "reqd", needs_reference ? 1 : 0);
			dialog.set_df_property("reference_no", "hidden", needs_reference ? 0 : 1);
			dialog.set_df_property("reference_date", "hidden", needs_reference ? 0 : 1);
			dialog.set_df_property(
				"bank_name",
				"hidden",
				["Demand Draft", "Cheque", "Bank Transfer"].includes(mode) ? 0 : 1
			);
		}

		function load_dues() {
			const selected = dialog.get_value("student");
			dialog.fields_dict.dues.df.data = [];
			dialog.fields_dict.dues.grid.refresh();
			dialog.fields_dict.student_info.$wrapper.empty();
			update_total();
			if (!selected) return;

			frappe
				.call({
					method: "hostel360.hostel_fees.desk.get_outstanding_invoices",
					args: { student: selected },
				})
				.then((r) => {
					const dues = r.message || [];
					dialog.fields_dict.dues.df.data = dues.map((row) => ({
						invoice: row.name,
						invoice_type: row.invoice_type,
						due_date: row.due_date,
						outstanding_amount: row.outstanding_amount,
						allocated_amount: !invoice || row.name === invoice ? row.outstanding_amount : 0,
					}));
					dialog.fields_dict.dues.grid.refresh();
					const outstanding = dues.reduce((sum, row) => sum + flt(row.outstanding_amount), 0);
					let summary = __("This student has no outstanding dues.");
					if (dues.length === 1) {
						summary = __("1 open invoice, {0} outstanding", [fmt(outstanding)]);
					} else if (dues.length > 1) {
						summary = __("{0} open invoices, {1} outstanding", [dues.length, fmt(outstanding)]);
					}
					dialog.fields_dict.student_info.$wrapper.html(`<div class="text-muted">${summary}</div>`);
					update_total();
				});
		}

		function submit(values) {
			const allocations = rows()
				.filter((row) => flt(row.allocated_amount) > 0)
				.map((row) => ({ invoice: row.invoice, allocated_amount: flt(row.allocated_amount) }));
			if (!allocations.length) {
				frappe.msgprint(__("Enter the amount received against at least one invoice."));
				return;
			}
			const over = rows().find((row) => flt(row.allocated_amount) > flt(row.outstanding_amount));
			if (over) {
				frappe.msgprint(
					__("The amount for {0} is more than its outstanding {1}.", [
						over.invoice,
						fmt(over.outstanding_amount),
					])
				);
				return;
			}

			frappe.confirm(
				__("Record {0} received by {1} from {2}? A receipt will be issued.", [
					`<strong>${fmt(total())}</strong>`,
					__(values.mode_of_payment),
					`<strong>${frappe.utils.escape_html(
						frappe.utils.get_link_title("Hostel Student", values.student) || values.student
					)}</strong>`,
				]),
				() => {
					frappe
						.call({
							method: "hostel360.hostel_fees.desk.record_payment",
							args: {
								student: values.student,
								mode_of_payment: values.mode_of_payment,
								allocations: allocations,
								payment_date: values.payment_date,
								reference_no: values.reference_no,
								reference_date: values.reference_date,
								bank_name: values.bank_name,
								remarks: values.remarks,
							},
							freeze: true,
							freeze_message: __("Issuing receipt..."),
						})
						.then((r) => {
							if (!r.message) return;
							dialog.hide();
							const receipt = r.message.payment;
							frappe.show_alert({
								message: __("Receipt {0} issued", [receipt]),
								indicator: "green",
							});
							frappe.msgprint({
								title: __("Payment recorded"),
								indicator: "green",
								message: __("Receipt {0} for {1} has been issued.", [
									frappe.utils.get_form_link("Hostel Payment", receipt, true),
									fmt(r.message.amount),
								]),
								primary_action: {
									label: __("Print Receipt"),
									action: () => {
										const url = `/printview?doctype=${encodeURIComponent(
											"Hostel Payment"
										)}&name=${encodeURIComponent(receipt)}&format=${encodeURIComponent(
											"Hostel Fee Receipt"
										)}&trigger_print=1&no_letterhead=1`;
										window.open(url, "_blank", "noopener");
									},
								},
							});
							on_success && on_success(receipt);
						});
				}
			);
		}

		dialog.show();
		toggle_reference();
		update_total();
		if (student) load_dues();
		return dialog;
	};

	frappe.ui.form.on("Hostel Fee Invoice", {
		setup(frm) {
			frm.set_query("fee_head", "items", () => ({ filters: { is_active: 1 } }));
			frm.set_query("fee_structure", () => ({ filters: { is_active: 1 } }));
		},

		refresh(frm) {
			if (frm.doc.docstatus !== 1) return;

			const outstanding = flt(frm.doc.outstanding_amount);
			if (outstanding > 0 && frappe.model.can_create("Hostel Payment")) {
				frm.add_custom_button(__("Record Payment"), () =>
					hostel360.fees.collect_fees({
						student: frm.doc.student,
						invoice: frm.doc.name,
						on_success: () => frm.reload_doc(),
					})
				).addClass("btn-primary");
			}
			if (outstanding > 0 && frm.perm[0] && frm.perm[0].write) {
				frm.add_custom_button(
					__("Send Reminder"),
					() =>
						frappe.confirm(__("Send a fee reminder to the student and parents now?"), () =>
							frappe
								.call({
									method: "hostel360.hostel_fees.desk.send_reminder",
									args: { invoice: frm.doc.name },
									freeze: true,
								})
								.then(() => {
									frappe.show_alert({ message: __("Reminder queued"), indicator: "green" });
									frm.reload_doc();
								})
						),
					__("Actions")
				);
			}
			if (flt(frm.doc.paid_amount) > 0) {
				frm.add_custom_button(
					__("Receipts"),
					() => frappe.set_route("List", "Hostel Payment", { invoice: frm.doc.name }),
					__("View")
				);
			}
		},

		fee_structure(frm) {
			if (!frm.doc.fee_structure || frm.doc.docstatus !== 0) return;
			frappe.db.get_doc("Hostel Fee Structure", frm.doc.fee_structure).then((structure) => {
				frm.clear_table("items");
				(structure.items || []).forEach((row) => {
					const item = frm.add_child("items");
					item.fee_head = row.fee_head;
					item.description = row.description;
					item.amount = row.amount;
				});
				frm.refresh_field("items");
				frm.set_value({
					due_date: structure.due_date,
					academic_year: structure.academic_year,
				});
				set_invoice_totals(frm);
			});
		},
	});

	frappe.ui.form.on("Hostel Fee Invoice Item", {
		amount: (frm) => set_invoice_totals(frm),
		items_remove: (frm) => set_invoice_totals(frm),
	});

	function set_invoice_totals(frm) {
		const total = (frm.doc.items || []).reduce((sum, row) => sum + flt(row.amount), 0);
		frm.set_value("total_amount", total);
		frm.set_value("outstanding_amount", total - flt(frm.doc.paid_amount));
	}
})();
