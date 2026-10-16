// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Exit Export Log", {
	refresh(frm) {
		const colours = { Exported: "blue", Verified: "green", "Verification Failed": "red" };
		if (frm.doc.purged_on) {
			frm.set_intro(
				__("The institution's data was deleted on {0} (certificate {1}).", [
					frappe.datetime.str_to_user(frm.doc.purged_on),
					frm.doc.certificate_id || "-",
				]),
				"blue"
			);
		} else if (frm.doc.status === "Exported") {
			frm.set_intro(
				__("Not verified yet. Run h360-verify-export on this site against the export before it can clear the way for deletion."),
				"orange"
			);
		}
		frm.page.set_indicator(__(frm.doc.status), colours[frm.doc.status] || "gray");
	},
});
