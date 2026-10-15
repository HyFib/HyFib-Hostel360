// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const MESS_INDENT_APPROVERS = ["Mess Manager", "Hostel Administrator", "Hostel Warden", "System Manager"];
const MESS_INDENT_BUYERS = ["Mess Manager", "Hostel Administrator", "System Manager"];

frappe.ui.form.on("Mess Indent", {
	setup(frm) {
		frm.set_query("mess", () => ({ filters: { is_active: 1 } }));
		frm.set_query("supplier", () => ({ filters: { is_active: 1 } }));
		frm.set_query("item", "items", () => ({ filters: { is_active: 1 } }));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.mess) {
			frm.add_custom_button(__("Get Items Below Reorder Level"), () => frm.events.get_low_stock_items(frm));
		}
		if (frm.doc.docstatus !== 1) return;

		if (frm.doc.status === "Pending Approval" && frappe.user.has_role(MESS_INDENT_APPROVERS)) {
			frm.add_custom_button(__("Approve"), () => frm.events.decide(frm, "approve"), __("Decision"));
			frm.add_custom_button(__("Reject"), () => frm.events.decide(frm, "reject"), __("Decision"));
			frm.page.set_inner_btn_group_as_primary(__("Decision"));
		}
		if (frm.doc.status === "Approved" && frappe.user.has_role(MESS_INDENT_BUYERS)) {
			frm.add_custom_button(__("Mark as Ordered"), () => frm.events.mark_ordered(frm));
		}
		if (["Approved", "Ordered"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Receive Goods"), () =>
				frappe.model.open_mapped_doc({
					method: "hostel360.hostel_mess.doctype.mess_indent.mess_indent.make_stock_receipt",
					frm,
				})
			);
		}
		if (frm.doc.status === "Rejected" && frm.doc.approval_remarks) {
			frm.set_intro(__("Rejected: {0}", [frappe.utils.escape_html(frm.doc.approval_remarks)]), "red");
		}
	},

	mess(frm) {
		(frm.doc.items || []).forEach((row) => row.item && mess_indent_fetch_item(frm, row.doctype, row.name));
	},

	get_low_stock_items(frm) {
		frappe
			.call({
				method: "hostel360.hostel_mess.doctype.mess_indent.mess_indent.get_items_below_reorder_level",
				args: { mess: frm.doc.mess },
				freeze: true,
			})
			.then((r) => {
				const rows = r.message || [];
				const existing = new Set((frm.doc.items || []).map((row) => row.item));
				const fresh = rows.filter((row) => !existing.has(row.item));
				if (!fresh.length) {
					frappe.show_alert({
						message: rows.length
							? __("All low-stock items are already in this indent.")
							: __("No item is at or below its reorder level."),
						indicator: "green",
					});
					return;
				}
				// Replace an untouched blank first row instead of leaving it behind.
				if (frm.doc.items?.length === 1 && !frm.doc.items[0].item) {
					frm.clear_table("items");
				}
				fresh.forEach((data) => {
					const row = frm.add_child("items", data);
					row.estimated_amount = flt(flt(row.required_qty) * flt(row.estimated_rate), 2);
				});
				frm.refresh_field("items");
				mess_indent_set_total(frm);
				frappe.show_alert({ message: __("{0} item(s) added", [fresh.length]), indicator: "blue" });
			});
	},

	decide(frm, action) {
		const rejecting = action === "reject";
		frappe.prompt(
			[
				{
					fieldname: "remarks",
					fieldtype: "Small Text",
					label: rejecting ? __("Reason for Rejection") : __("Remarks"),
					reqd: rejecting ? 1 : 0,
				},
			],
			(values) => {
				frm.call({ doc: frm.doc, method: action, args: { remarks: values.remarks }, freeze: true }).then(() =>
					frm.reload_doc()
				);
			},
			rejecting ? __("Reject Indent") : __("Approve Indent"),
			rejecting ? __("Reject") : __("Approve")
		);
	},

	mark_ordered(frm) {
		frappe.prompt(
			[
				{
					fieldname: "supplier",
					fieldtype: "Link",
					label: __("Supplier"),
					options: "Mess Supplier",
					default: frm.doc.supplier,
					get_query: () => ({ filters: { is_active: 1 } }),
				},
			],
			(values) => {
				frm.call({ doc: frm.doc, method: "mark_ordered", args: { supplier: values.supplier }, freeze: true }).then(
					() => frm.reload_doc()
				);
			},
			__("Mark as Ordered"),
			__("Save")
		);
	},
});

frappe.ui.form.on("Mess Indent Item", {
	item(frm, cdt, cdn) {
		mess_indent_fetch_item(frm, cdt, cdn);
	},
	required_qty(frm, cdt, cdn) {
		mess_indent_set_amount(frm, cdt, cdn);
	},
	estimated_rate(frm, cdt, cdn) {
		mess_indent_set_amount(frm, cdt, cdn);
	},
	items_remove(frm) {
		mess_indent_set_total(frm);
	},
});

function mess_indent_fetch_item(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row.item || !frm.doc.mess) return;
	frappe
		.call({
			method: "hostel360.hostel_mess.doctype.mess_stock_entry.mess_stock_entry.get_item_details",
			args: { mess: frm.doc.mess, item: row.item },
		})
		.then((r) => {
			const details = r.message || {};
			const values = { current_stock: details.available_qty, uom: details.uom };
			if (!row.estimated_rate) {
				values.estimated_rate = details.last_purchase_rate || details.valuation_rate;
			}
			return frappe.model.set_value(cdt, cdn, values);
		})
		.then(() => mess_indent_set_amount(frm, cdt, cdn));
}

function mess_indent_set_amount(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "estimated_amount", flt(flt(row.required_qty) * flt(row.estimated_rate), 2));
	mess_indent_set_total(frm);
}

function mess_indent_set_total(frm) {
	const total = (frm.doc.items || []).reduce((sum, row) => sum + flt(row.estimated_amount), 0);
	frm.set_value("total_estimated_amount", flt(total, 2));
}
