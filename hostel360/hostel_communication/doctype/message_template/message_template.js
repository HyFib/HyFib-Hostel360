// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Message Template", {
	refresh(frm) {
		if (!frm.doc.event || !frm.doc.channel) return;

		frm.add_custom_button(__("Preview"), () => show_preview(frm));

		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Restore Default Wording"),
				() => restore_default(frm),
				__("Actions")
			);
			frm.add_custom_button(
				__("Message Log"),
				() => frappe.set_route("List", "Message Log", { template: frm.doc.name }),
				__("View")
			);
		}

		if (frm.doc.channel === "SMS") {
			frm.set_intro(
				__(
					"Register this exact wording on the DLT portal, replacing each {{ ... }} with {#var#}. Keep SMS in plain English characters (write Rs. instead of the rupee symbol) so it fits in a single message."
				),
				"blue"
			);
		} else if (frm.doc.channel === "WhatsApp") {
			frm.set_intro(
				__(
					"Submit this wording in WhatsApp Manager with the template parameters as {{1}}, {{2}} and so on, in the order listed below."
				),
				"blue"
			);
		}
	},
});

function show_preview(frm) {
	frm.call({ method: "preview", doc: frm.doc, freeze: true }).then((r) => {
		const data = r.message;
		if (!data) return;

		const parts = [];
		if (data.subject) {
			parts.push(`<p class="text-muted small mb-1">${__("Subject")}</p>
				<p class="bold">${frappe.utils.escape_html(data.subject)}</p>`);
		}
		const body =
			frm.doc.channel === "Email"
				? `<div class="border rounded p-3">${frappe.dom.remove_script_and_style(data.body)}</div>`
				: `<pre class="border rounded p-3" style="white-space: pre-wrap">${frappe.utils.escape_html(data.body)}</pre>`;
		parts.push(`<p class="text-muted small mb-1">${__("Message")}</p>${body}`);

		if (data.stats) {
			const colour = data.stats.segments > 1 || data.stats.unicode ? "orange" : "green";
			parts.push(`<p><span class="indicator-pill ${colour}">
				${__("{0} characters, {1} SMS", [data.stats.length, data.stats.segments])}
				${data.stats.unicode ? " · " + __("Unicode") : ""}
			</span></p>`);
		}
		if (data.parameters && data.parameters.length) {
			const rows = data.parameters
				.map((p, i) => `<tr><td class="text-muted">{{${i + 1}}}</td><td>${frappe.utils.escape_html(p)}</td></tr>`)
				.join("");
			parts.push(`<p class="text-muted small mb-1">${__("Template Parameters")}</p>
				<table class="table table-sm table-bordered">${rows}</table>`);
		}

		frappe.msgprint({
			title: __("Preview with sample values"),
			message: parts.join(""),
			wide: true,
		});
	});
}

function restore_default(frm) {
	frm.call({ method: "get_default_wording", doc: frm.doc }).then((r) => {
		const wording = r.message;
		if (!wording) {
			frappe.msgprint(__("There is no default wording for this event and channel."));
			return;
		}
		frappe.confirm(
			__("Replace the subject, body and parameters with the default wording? Save the form to keep the change."),
			() => {
				["subject", "body", "template_parameters"].forEach((field) => {
					if (wording[field] !== undefined) frm.set_value(field, wording[field]);
				});
			}
		);
	});
}
