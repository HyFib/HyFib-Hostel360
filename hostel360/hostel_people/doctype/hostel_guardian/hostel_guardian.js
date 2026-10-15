// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hostel Guardian", {
	refresh(frm) {
		frm.trigger("render_wards");
		if (frm.is_new()) return;

		if (!frm.doc.user && frm.perm[0]?.write) {
			frm.add_custom_button(
				__("Create Portal User"),
				() =>
					frm
						.call({ doc: frm.doc, method: "create_portal_user", freeze: true })
						.then((r) => {
							frappe.show_alert({
								message: __("Portal user {0} created.", [r.message]),
								indicator: "green",
							});
							frm.reload_doc();
						}),
				__("Actions")
			);
		}
		if (frm.doc.validation_status === "Pending") {
			frm.dashboard.set_headline_alert(
				__("Validated automatically when a linked student is validated."),
				"orange"
			);
		}
	},

	render_wards(frm) {
		const wrapper = frm.get_field("wards_html").$wrapper;
		if (frm.is_new()) {
			wrapper.html(
				`<p class="text-muted small">${__(
					"Link this guardian from the Guardians table of a student."
				)}</p>`
			);
			return;
		}
		frm.call({ doc: frm.doc, method: "get_wards" }).then((r) => {
			const wards = r.message || [];
			if (!wards.length) {
				wrapper.html(`<p class="text-muted small">${__("Not linked to any student yet.")}</p>`);
				return;
			}
			const esc = frappe.utils.escape_html;
			const rows = wards
				.map(
					(w) => `<tr>
						<td><a href="${frappe.utils.get_form_link("Hostel Student", w.name)}">${esc(w.full_name)}</a>
							<div class="text-muted small">${esc(w.name)}</div></td>
						<td>${esc(__(w.relation || ""))}${w.is_primary ? ` · ${__("Primary")}` : ""}</td>
						<td>${esc(w.current_room || __("Not allotted"))}</td>
						<td>${esc(__(w.validation_status))}</td>
					</tr>`
				)
				.join("");
			wrapper.html(`<table class="table table-sm table-bordered">
				<thead><tr><th>${__("Student")}</th><th>${__("Relation")}</th><th>${__("Room")}</th><th>${__("Validation")}</th></tr></thead>
				<tbody>${rows}</tbody></table>`);
		});
	},
});
