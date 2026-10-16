// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Message Log", {
	refresh(frm) {
		const colours = { Sent: "green", Queued: "blue", Failed: "red", Skipped: "gray" };
		frm.page.set_indicator(__(frm.doc.status), colours[frm.doc.status] || "gray");

		if (frm.doc.__onload && frm.doc.__onload.can_retry) {
			frm.add_custom_button(__("Retry Now"), () => {
				frm.call({ method: "retry_now", doc: frm.doc, freeze: true }).then((r) => {
					if (!r.message) return;
					frappe.show_alert({
						message: r.message.ok ? __("Message sent.") : __("Still failing: {0}", [r.message.error || ""]),
						indicator: r.message.ok ? "green" : "red",
					}, 7);
					frm.reload_doc();
				});
			});
		}

		if (frm.doc.reference_doctype && frm.doc.reference_name) {
			frm.add_custom_button(__("Open {0}", [__(frm.doc.reference_doctype)]), () =>
				frappe.set_route("Form", frm.doc.reference_doctype, frm.doc.reference_name)
			);
		}
	},
});
