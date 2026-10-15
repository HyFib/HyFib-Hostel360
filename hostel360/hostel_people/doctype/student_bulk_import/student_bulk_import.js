// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const H360_IMPORT_METHOD = "hostel360.hostel_people.doctype.student_bulk_import.student_bulk_import.";

frappe.ui.form.on("Student Bulk Import", {
	setup(frm) {
		frm.set_query("academic_year", () => ({ order_by: "start_date desc" }));
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.academic_year && frappe.boot.hostel360?.current_academic_year) {
			frm.set_value("academic_year", frappe.boot.hostel360.current_academic_year);
		}
	},

	refresh(frm) {
		frm.trigger("render_preview");

		frm.add_custom_button(
			__("Excel Template"),
			() => window.open(frappe.urllib.get_full_url(`/api/method/${H360_IMPORT_METHOD}download_template?file_format=xlsx`)),
			__("Download")
		);
		frm.add_custom_button(
			__("CSV Template"),
			() => window.open(frappe.urllib.get_full_url(`/api/method/${H360_IMPORT_METHOD}download_template?file_format=csv`)),
			__("Download")
		);

		if (frm.is_new()) {
			frm.set_intro(__("Download the template, fill one student per row, attach it and save."), "blue");
			return;
		}

		if (frm.doc.errors_json) {
			frm.add_custom_button(
				__("Rows to Fix"),
				() =>
					window.open(
						frappe.urllib.get_full_url(
							`/api/method/${H360_IMPORT_METHOD}download_error_report?name=${encodeURIComponent(frm.doc.name)}`
						)
					),
				__("Download")
			);
		}

		if (frm.doc.import_file && frm.doc.status !== "Importing") {
			frm.add_custom_button(frm.doc.status === "Draft" ? __("Validate File") : __("Validate Again"), () =>
				frm
					.call({ doc: frm.doc, method: "validate_file", freeze: true, freeze_message: __("Checking every row...") })
					.then(() => frm.reload_doc())
			);
		}

		if (["Validated", "Completed with Errors", "Failed"].includes(frm.doc.status)) {
			const summary = frm.events.get_validation(frm)?.summary || {};
			const label = frm.doc.status === "Validated" ? __("Start Import") : __("Import Again");
			frm.page.set_primary_action(label, () => {
				const message =
					summary.errors && frm.doc.status === "Validated"
						? __("{0} rows have errors and will be skipped. Import the other {1} rows?", [
								summary.errors,
								(summary.rows || 0) - summary.errors,
						  ])
						: __("Import {0} rows now?", [frm.doc.total_rows || summary.rows || 0]);
				frappe.confirm(message, () =>
					frm.call({ doc: frm.doc, method: "start_import", freeze: true }).then(() => {
						frappe.show_alert({
							message: __("Import started. You can leave this page; progress is saved."),
							indicator: "blue",
						});
						frm.reload_doc();
					})
				);
			});
		}

		if (frm.doc.status === "Importing") {
			frm.set_intro(__("Import in progress. This page updates when it finishes."), "orange");
		} else if (frm.doc.status === "Completed") {
			frm.set_intro(
				__("{0} rows imported, {1} skipped.", [frm.doc.imported_rows || 0, frm.doc.skipped_rows || 0]),
				"green"
			);
		} else if (frm.doc.status === "Completed with Errors") {
			frm.set_intro(
				__("{0} rows imported, {1} failed. Download Rows to Fix, correct them and import that file.", [
					frm.doc.imported_rows || 0,
					frm.doc.failed_rows || 0,
				]),
				"orange"
			);
		}
	},

	import_file(frm) {
		if (frm.doc.import_file && !frm.is_new()) {
			frm.save().then(() =>
				frm
					.call({ doc: frm.doc, method: "validate_file", freeze: true, freeze_message: __("Checking every row...") })
					.then(() => frm.reload_doc())
			);
		}
	},

	get_validation(frm) {
		try {
			return frm.doc.validation_json ? JSON.parse(frm.doc.validation_json) : null;
		} catch (e) {
			return null;
		}
	},

	render_preview(frm) {
		const wrapper = frm.get_field("preview_html").$wrapper;
		const data = frm.events.get_validation(frm);
		if (!data) {
			wrapper.html(
				`<p class="text-muted small">${__("Validate the file to see every row and its problems here.")}</p>`
			);
			return;
		}
		const esc = frappe.utils.escape_html;
		const s = data.summary || {};
		const stat = (label, value, colour) =>
			`<span class="indicator-pill ${colour}" style="margin-right:6px">${label}: ${value || 0}</span>`;
		const rows = (data.rows || [])
			.map((r) => {
				const problems = (r.errors || []).map((e) => `<div class="text-danger">${esc(e)}</div>`);
				const notes = (r.warnings || []).map((w) => `<div class="text-warning">${esc(w)}</div>`);
				const colour = r.errors?.length ? "red" : r.action === "Skip" ? "gray" : r.action === "Update" ? "blue" : "green";
				const action = r.errors?.length ? __("Error") : __(r.action);
				return `<tr>
					<td>${r.row}</td>
					<td>${esc(r.register_number || "")}</td>
					<td>${esc(r.full_name || "")}</td>
					<td><span class="indicator-pill ${colour}">${esc(action)}</span></td>
					<td>${esc(r.bed || "")}</td>
					<td>${problems.join("")}${notes.join("")}</td>
				</tr>`;
			})
			.join("");
		const ignored = (data.ignored_columns || []).length
			? `<p class="text-muted small">${__("Ignored columns")}: ${esc(data.ignored_columns.join(", "))}</p>`
			: "";
		const truncated =
			(s.rows || 0) > (data.rows || []).length
				? `<p class="text-muted small">${__("Showing the first rows and every row with an error.")}</p>`
				: "";
		wrapper.html(`
			<div style="margin-bottom:10px">
				${stat(__("Rows"), s.rows, "blue")}
				${stat(__("New"), s.create, "green")}
				${stat(__("Update"), s.update, "blue")}
				${stat(__("Skip"), s.skip, "gray")}
				${stat(__("Errors"), s.errors, s.errors ? "red" : "gray")}
				${stat(__("Warnings"), s.warnings, s.warnings ? "orange" : "gray")}
				${stat(__("Bed Allotments"), s.allotments, "purple")}
			</div>
			${ignored}${truncated}
			<div style="max-height:480px;overflow:auto">
			<table class="table table-sm table-bordered" style="font-size:var(--text-sm)">
				<thead><tr>
					<th>${__("Row")}</th><th>${__("Register No.")}</th><th>${__("Name")}</th>
					<th>${__("Action")}</th><th>${__("Bed")}</th><th>${__("Problems")}</th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table></div>`);
	},
});
