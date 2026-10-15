// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const MESS_BILL_RUN_METHOD = "hostel360.hostel_mess.doctype.mess_bill_run.mess_bill_run";

frappe.ui.form.on("Mess Bill Run", {
	setup(frm) {
		frm.set_query("mess", () => ({ filters: { is_active: 1 } }));
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.from_date) {
			// Default to the previous calendar month, the usual billing period.
			const first_of_this_month = frappe.datetime.month_start();
			const from = frappe.datetime.add_months(first_of_this_month, -1);
			frm.set_value("from_date", from);
			frm.set_value("to_date", frappe.datetime.add_days(first_of_this_month, -1));
		}
		if (!frm.__mess_bill_run_listeners) {
			frm.__mess_bill_run_listeners = true;
			frappe.realtime.on("mess_bill_run_computed", (data) => {
				if (data.name !== frm.doc.name) return;
				frappe.show_alert({
					message: __("{0} bills computed at {1} per day", [data.bills_count, format_currency(data.daily_rate)]),
					indicator: "green",
				});
				if (data.enrolment_changes) {
					frappe.msgprint({
						title: __("Enrolment Changes"),
						message: __(
							"The mess enrolment of {0} resident(s) changed during or after this period. They are charged only for the days they were enrolled; please check their bills before publishing.",
							[data.enrolment_changes]
						),
						indicator: "orange",
					});
				}
				frm.reload_doc();
			});
			frappe.realtime.on("mess_bill_run_compute_failed", (data) => {
				if (data.name !== frm.doc.name) return;
				frappe.msgprint({ title: __("Could Not Compute Bills"), message: data.error, indicator: "red" });
			});
			frappe.realtime.on("mess_bill_run_action_done", (data) => {
				if (data.name !== frm.doc.name) return;
				frappe.show_alert({
					message:
						data.action === "submit"
							? __("Mess bills published.")
							: __("Bill run cancelled and its invoices withdrawn."),
					indicator: "green",
				});
				frm.reload_doc();
			});
			frappe.realtime.on("mess_bill_run_action_failed", (data) => {
				if (data.name !== frm.doc.name) return;
				frappe.msgprint({
					title: data.action === "submit" ? __("Could Not Publish Bills") : __("Could Not Cancel Bill Run"),
					message: data.error,
					indicator: "red",
				});
				frm.reload_doc();
			});
		}
	},

	before_submit(frm) {
		return frm.events.run_in_background(frm, "submit");
	},

	before_cancel(frm) {
		return frm.events.run_in_background(frm, "cancel");
	},

	run_in_background(frm, action) {
		// Large runs create or cancel one invoice per bill, which takes too long for a page
		// request: hand them to a background worker instead of the standard submit/cancel.
		if (!frm.doc.__onload?.runs_in_background) return;
		frappe.validated = false;
		// Resolve whatever happens, so the form re-enables its buttons after an error message.
		return new Promise((resolve) => {
			frappe.call({
				method: `${MESS_BILL_RUN_METHOD}.start_action`,
				args: { name: frm.doc.name, action },
				freeze: true,
				callback: () => frm.reload_doc(),
				always: () => resolve(),
			});
		});
	},

	refresh(frm) {
		frm.events.show_summary(frm);

		if (frm.doc.__onload?.action_queued) {
			frm.set_intro(
				__("Bills are being published or cancelled in the background. This page refreshes when it is done."),
				"blue"
			);
			return;
		}

		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			const button = frm.add_custom_button(__("Compute Bills"), () => frm.events.compute(frm));
			if (frm.doc.status !== "Computed") button.addClass("btn-primary");
		}
		if (!frm.is_new() && frm.doc.bills_count) {
			frm.add_custom_button(
				__("Bills"),
				() => frappe.set_route("List", "Mess Bill", { mess_bill_run: frm.doc.name }),
				__("View")
			);
		}
		if (frm.doc.mess && frm.doc.from_date && frm.doc.to_date) {
			frm.add_custom_button(
				__("Consumption"),
				() =>
					frappe.set_route("query-report", "Mess Consumption", {
						mess: frm.doc.mess,
						from_date: frm.doc.from_date,
						to_date: frm.doc.to_date,
						granularity: "Daily",
					}),
				__("View")
			);
		}

		if (frm.doc.docstatus === 0 && frm.doc.status === "Draft" && !frm.is_new()) {
			frm.set_intro(
				__(
					"Compute Bills divides the period's mess cost by the total student-days and prepares one bill per resident."
				),
				"blue"
			);
		} else if (frm.doc.docstatus === 0 && frm.doc.status === "Computed") {
			frm.set_intro(
				__(
					"Review the bills, then Submit to publish them: a fee invoice is raised for every student and parents are notified."
				),
				"orange"
			);
		}
	},

	compute(frm) {
		const run = () =>
			frappe
				.call({ method: `${MESS_BILL_RUN_METHOD}.compute`, args: { name: frm.doc.name }, freeze: true })
				.then((r) => {
					const result = r.message || {};
					if (result.queued) {
						frappe.show_alert({
							message: __("Computing bills in the background. This form will refresh when done."),
							indicator: "blue",
						});
					} else {
						frappe.show_alert({
							message: __("{0} bills computed at {1} per day", [
								result.bills_count,
								format_currency(result.daily_rate),
							]),
							indicator: "green",
						});
						frm.reload_doc();
					}
				});
		if (frm.is_dirty()) {
			frm.save().then(run);
		} else {
			run();
		}
	},

	show_summary(frm) {
		if (!frm.doc.daily_rate) return;
		frm.dashboard.add_indicator(__("Daily Rate: {0}", [format_currency(frm.doc.daily_rate)]), "blue");
		frm.dashboard.add_indicator(__("Student-Days: {0}", [frm.doc.total_student_days]), "grey");
		frm.dashboard.add_indicator(__("Total Billed: {0}", [format_currency(frm.doc.total_billed)]), "green");

		const summary = frm.doc.__onload?.invoice_summary || [];
		if (summary.length) {
			const paid = summary.filter((row) => row.status === "Paid").reduce((n, row) => n + row.count, 0);
			const total = summary.reduce((n, row) => n + row.count, 0);
			const outstanding = summary.reduce((n, row) => n + flt(row.outstanding), 0);
			frm.dashboard.add_indicator(__("Invoices Paid: {0} of {1}", [paid, total]), paid === total ? "green" : "orange");
			if (outstanding) {
				frm.dashboard.add_indicator(__("Outstanding: {0}", [format_currency(outstanding)]), "red");
			}
		}
	},
});
