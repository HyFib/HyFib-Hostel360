// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

(() => {
	const PUBLISH_INDICATORS = {
		"Not Published": "gray",
		Queued: "blue",
		"In Progress": "blue",
		Completed: "green",
		"Completed with Errors": "orange",
		Failed: "red",
	};

	frappe.ui.form.on("Hostel Fee Structure", {
		setup(frm) {
			frm.set_query("fee_head", "items", () => ({ filters: { is_active: 1 } }));
		},

		onload(frm) {
			// One listener per session; reopening the form must not stack handlers.
			frappe.realtime.off("hostel360_dues_published");
			frappe.realtime.on("hostel360_dues_published", (data) => {
				if (!cur_frm || cur_frm.doctype !== "Hostel Fee Structure" || data.structure !== cur_frm.doc.name) {
					return;
				}
				frappe.show_alert({
					message: __("{0} invoices published, {1} already invoiced, {2} failed", [
						data.created,
						data.skipped,
						data.failed,
					]),
					indicator: data.failed ? "orange" : "green",
				});
				cur_frm.reload_doc();
			});
		},

		refresh(frm) {
			if (frm.is_new()) return;

			const status = frm.doc.publish_status || "Not Published";
			frm.page.set_indicator(__(status), PUBLISH_INDICATORS[status] || "gray");

			if (frm.doc.is_active && frappe.model.can_create("Hostel Fee Invoice")) {
				frm.add_custom_button(__("Publish Dues"), () => publish_dues(frm)).addClass("btn-primary");
			}
			frm.add_custom_button(__("View Invoices"), () =>
				frappe.set_route("List", "Hostel Fee Invoice", { fee_structure: frm.doc.name })
			);
			if (frm.doc.publish_log) {
				frm.dashboard.set_headline(frappe.utils.escape_html(frm.doc.publish_log), PUBLISH_INDICATORS[status]);
			}
		},
	});

	frappe.ui.form.on("Hostel Fee Structure Item", {
		amount: (frm) => set_total(frm),
		items_remove: (frm) => set_total(frm),
	});

	function set_total(frm) {
		const total = (frm.doc.items || []).reduce((sum, row) => sum + flt(row.amount), 0);
		frm.set_value("total_amount", total);
	}

	function publish_dues(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save the fee structure before publishing dues."));
			return;
		}

		const show = (reissue_cancelled) =>
			frm.call("get_publish_preview", { reissue_cancelled: reissue_cancelled ? 1 : 0 }).then((r) => {
				const preview = r.message;
				if (preview.is_running) {
					frappe.msgprint(__("Dues for this structure are already being published."));
					return;
				}
				confirm_publish(frm, preview, reissue_cancelled, show);
			});
		show(false);
	}

	function confirm_publish(frm, preview, reissue_cancelled, reload) {
		const fmt = (value) => format_currency(value, "INR");
		const past_due = frappe.datetime.get_day_diff(frm.doc.due_date, frappe.datetime.get_today()) < 0;
		const lines = [
			[__("Matching active, validated students"), preview.matching_students],
			[__("Already invoiced (skipped)"), preview.already_invoiced],
			[__("Invoices to create"), `<strong>${preview.to_invoice}</strong>`],
			[__("Amount per student"), fmt(preview.amount_per_student)],
			[__("Total to be billed"), `<strong>${fmt(preview.total_amount)}</strong>`],
			[__("Due date"), frappe.datetime.str_to_user(frm.doc.due_date)],
		];

		const dialog = new frappe.ui.Dialog({
			title: __("Publish Dues"),
			fields: [
				{
					fieldname: "summary",
					fieldtype: "HTML",
					options: `
						<table class="table table-sm">
							${lines.map(([label, value]) => `<tr><td>${label}</td><td class="text-right">${value}</td></tr>`).join("")}
						</table>
						${
							past_due
								? `<p class="text-danger">${__(
										"The due date has passed. These invoices will be overdue immediately."
								  )}</p>`
								: ""
						}
						<p class="text-muted">${
							preview.to_invoice
								? __("Students and parents are notified as soon as each invoice is created.")
								: __("Every matching student already has an invoice from this structure.")
						}</p>`,
				},
				{
					fieldname: "reissue_cancelled",
					fieldtype: "Check",
					label: __("Re-issue to {0} students whose earlier invoice was cancelled", [
						preview.cancelled_only,
					]),
					default: reissue_cancelled ? 1 : 0,
					hidden: preview.cancelled_only || reissue_cancelled ? 0 : 1,
					onchange: () => {
						dialog.hide();
						reload(dialog.get_value("reissue_cancelled"));
					},
				},
			],
			primary_action_label: preview.to_invoice
				? __("Publish {0} Invoices", [preview.to_invoice])
				: __("Close"),
			primary_action: (values) => {
				dialog.hide();
				if (!preview.to_invoice) return;
				frm.call("publish_dues", { reissue_cancelled: values.reissue_cancelled ? 1 : 0 }).then(() => {
					frappe.show_alert({
						message: __("Publishing started. You can keep working; this form updates when it finishes."),
						indicator: "blue",
					});
					frm.reload_doc();
				});
			},
		});
		dialog.show();
	}
})();
