// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

(() => {
	frappe.ui.form.on("Hostel Payment Settings", {
		refresh(frm) {
			render_webhook_help(frm);

			if (frappe.user.has_role(["Hostel Administrator", "System Manager"])) {
				frm.add_custom_button(__("Test Connection"), () => {
					if (frm.is_dirty()) {
						frappe.msgprint(__("Save the settings before testing the connection."));
						return;
					}
					frm.call({ doc: frm.doc, method: "test_connection", freeze: true }).then((r) => {
						if (r.message) {
							frappe.msgprint({
								title: __("Connection successful"),
								indicator: "green",
								message: r.message.message,
							});
						}
					});
				});
			}
		},

		test_mode(frm) {
			if (!frm.doc.test_mode && frm.doc.enabled) {
				frappe.msgprint({
					title: __("Live payments"),
					indicator: "orange",
					message: __("With Test Mode off, students will be charged real money. Use live keys (rzp_live_...)."),
				});
			}
		},
	});

	function render_webhook_help(frm) {
		const onload = frm.doc.__onload || {};
		const url = onload.webhook_url || "";
		const events = (onload.webhook_events || []).map((e) => `<code>${frappe.utils.escape_html(e)}</code>`);
		frm.get_field("webhook_help").$wrapper.html(`
			<div class="text-muted small">
				<p>${__("In the Razorpay dashboard, add a webhook with:")}</p>
				<p><strong>${__("URL")}</strong>: <code>${frappe.utils.escape_html(url)}</code></p>
				<p><strong>${__("Events")}</strong>: ${events.join(", ")}</p>
				<p>${__("Enter the same secret in the Webhook Secret field above.")}</p>
			</div>
		`);
	}
})();
