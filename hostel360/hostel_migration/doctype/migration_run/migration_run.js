// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Migration Run", {
	refresh(frm) {
		if (frm.is_new()) {
			frm.set_intro(
				__(
					"Copy the legacy export to the site's private/migration folder, give its folder name, paste the mapping and save. Start with a dry run: it checks every row and rolls everything back."
				),
				"blue"
			);
			return;
		}

		frm.trigger("show_summary");
		frm.trigger("add_report_buttons");

		if (frm.doc.status === "Draft") {
			const label = frm.doc.dry_run ? __("Start Dry Run") : __("Start Migration");
			frm.page.set_primary_action(label, () => frm.trigger("start"));
		}

		if (frm.doc.status === "Running") {
			frm.set_intro(__("This run is in progress. The page refreshes when it finishes."), "blue");
			frm.events.poll_until_finished(frm);
		}

		// Offered while "Running" too: if the server restarted mid-run, the server marks the
		// stale run as failed and starts a new one; if it is really running it says so.
		if (["Running", "Completed", "Completed with Errors", "Failed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Dry Run Again"), () => frm.events.run_again(frm, 1), __("Run Again"));
			frm.add_custom_button(__("Migrate for Real"), () => frm.events.run_again(frm, 0), __("Run Again"));
		}
	},

	start(frm) {
		const go = () =>
			frm.call({ doc: frm.doc, method: "start", freeze: true }).then(() => {
				frappe.show_alert({
					message: __("Run queued. You can leave this page; the report is attached when it finishes."),
					indicator: "blue",
				});
				setTimeout(() => frm.reload_doc(), 1500);
			});

		if (frm.doc.dry_run) {
			go();
			return;
		}
		frappe.confirm(
			__(
				"This migration keeps every change it makes. Has a dry run of the same files and mapping completed cleanly?"
			),
			go
		);
	},

	run_again(frm, dry_run) {
		frm.call({ doc: frm.doc, method: "run_again", args: { dry_run }, freeze: true }).then((r) => {
			if (r.message) frappe.set_route("Form", "Migration Run", r.message);
		});
	},

	poll_until_finished(frm) {
		if (frm._h360_poll) return;
		frm._h360_poll = setInterval(() => {
			if (frm.doc.doctype !== "Migration Run" || frm.is_dirty()) return;
			frappe.db.get_value("Migration Run", frm.doc.name, "status").then((r) => {
				if (r.message && r.message.status !== "Running") {
					clearInterval(frm._h360_poll);
					frm._h360_poll = null;
					frm.reload_doc();
				}
			});
		}, 5000);
	},

	show_summary(frm) {
		if (!frm.doc.started_on) return;
		const failed = frm.doc.total_failed || 0;
		const parts = [
			__("{0} source rows", [frm.doc.total_source_rows || 0]),
			__("{0} created", [frm.doc.total_created || 0]),
			__("{0} updated", [frm.doc.total_updated || 0]),
			__("{0} unchanged", [frm.doc.total_skipped || 0]),
			__("{0} failed", [failed]),
		];
		const colour =
			frm.doc.reconciliation_status === "Matched" && !failed ? "green" : frm.doc.status === "Running" ? "blue" : "orange";
		let message = parts.join(" · ");
		if (frm.doc.reconciliation_status && frm.doc.reconciliation_status !== "Not Run") {
			message += " · " + __("Reconciliation: {0}", [__(frm.doc.reconciliation_status)]);
		}
		if (frm.doc.dry_run && frm.doc.status !== "Running") {
			message += " · " + __("dry run, nothing was kept");
		}
		frm.dashboard.set_headline_alert(message, colour);
	},

	add_report_buttons(frm) {
		const reports = [
			["report_file", __("Excel Report")],
			["report_markdown", __("Markdown Report")],
			["report_json", __("JSON Report")],
		];
		for (const [field, label] of reports) {
			if (frm.doc[field]) {
				frm.add_custom_button(label, () => window.open(frm.doc[field]), __("Download"));
			}
		}
	},
});
