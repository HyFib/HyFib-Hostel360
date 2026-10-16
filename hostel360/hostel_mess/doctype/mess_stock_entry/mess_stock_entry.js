// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const MESS_OUTGOING_TYPES = ["Issue", "Return to Supplier"];

frappe.ui.form.on("Mess Stock Entry", {
	setup(frm) {
		frm.set_query("mess", () => ({ filters: { is_active: 1 } }));
		frm.set_query("supplier", () => ({ filters: { is_active: 1 } }));
		frm.set_query("indent", () => ({
			filters: {
				mess: frm.doc.mess,
				docstatus: 1,
				status: ["in", ["Approved", "Ordered"]],
			},
		}));
		frm.set_query("item", "items", () => ({ filters: { is_active: 1 } }));
	},

	refresh(frm) {
		frm.events.toggle_rate_editing(frm);
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(
				__("Stock Ledger"),
				() => frappe.set_route("List", "Mess Stock Ledger Entry", { voucher_no: frm.doc.name }),
				__("View")
			);
		}
		if (frm.doc.mess) {
			frm.add_custom_button(
				__("Stock Balance"),
				() => frappe.set_route("query-report", "Mess Stock Balance", { mess: frm.doc.mess }),
				__("View")
			);
		}
		if (frm.doc.docstatus === 0 && frm.doc.entry_type === "Adjustment") {
			frm.set_intro(
				__("Enter a positive quantity for stock found and a negative quantity for shortage or wastage."),
				"blue"
			);
		}
	},

	entry_type(frm) {
		frm.events.toggle_rate_editing(frm);
		frm.events.refresh_rows(frm);
		frm.refresh_fields();
	},

	mess(frm) {
		frm.events.refresh_rows(frm);
	},

	posting_date(frm) {
		frm.events.refresh_rows(frm);
	},

	posting_time(frm) {
		frm.events.refresh_rows(frm);
	},

	toggle_rate_editing(frm) {
		const outgoing = MESS_OUTGOING_TYPES.includes(frm.doc.entry_type);
		frm.fields_dict.items.grid.update_docfield_property("rate", "read_only", outgoing ? 1 : 0);
	},

	refresh_rows(frm) {
		(frm.doc.items || []).forEach((row) => {
			if (row.item) mess_stock_entry_fetch_item(frm, row.doctype, row.name);
		});
	},
});

frappe.ui.form.on("Mess Stock Entry Item", {
	item(frm, cdt, cdn) {
		mess_stock_entry_fetch_item(frm, cdt, cdn);
	},
	qty(frm, cdt, cdn) {
		mess_stock_entry_set_amount(frm, cdt, cdn);
	},
	rate(frm, cdt, cdn) {
		mess_stock_entry_set_amount(frm, cdt, cdn);
	},
	items_remove(frm) {
		mess_stock_entry_set_total(frm);
	},
});

function mess_stock_entry_fetch_item(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row.item || !frm.doc.mess) return;
	frappe
		.call({
			method: "hostel360.hostel_mess.doctype.mess_stock_entry.mess_stock_entry.get_item_details",
			args: {
				mess: frm.doc.mess,
				item: row.item,
				posting_date: frm.doc.posting_date,
				posting_time: frm.doc.posting_time,
			},
		})
		.then((r) => {
			const details = r.message || {};
			const values = {
				available_qty: details.available_qty,
				valuation_rate: details.valuation_rate,
				uom: details.uom,
			};
			if (MESS_OUTGOING_TYPES.includes(frm.doc.entry_type)) {
				values.rate = details.valuation_rate;
			} else if (frm.doc.entry_type === "Adjustment" && !row.rate) {
				values.rate = details.valuation_rate;
			} else if (frm.doc.entry_type === "Receipt" && !row.rate) {
				values.rate = details.last_purchase_rate;
			}
			return frappe.model.set_value(cdt, cdn, values);
		})
		.then(() => mess_stock_entry_set_amount(frm, cdt, cdn));
}

function mess_stock_entry_set_amount(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const amount = flt(Math.abs(flt(row.qty)) * flt(row.rate), precision("amount", row));
	if (amount !== flt(row.amount)) {
		frappe.model.set_value(cdt, cdn, "amount", amount);
	}
	mess_stock_entry_set_total(frm);
}

function mess_stock_entry_set_total(frm) {
	const total = (frm.doc.items || []).reduce((sum, row) => sum + flt(row.amount), 0);
	frm.set_value("total_amount", flt(total, precision("total_amount")));
}
