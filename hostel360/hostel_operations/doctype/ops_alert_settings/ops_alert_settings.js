// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Ops Alert Settings", {
	refresh(frm) {
		const is_ops = frappe.user.has_role(["HyFib Operations", "System Manager"]);
		render_integration_help(frm, is_ops);
		if (!is_ops) {
			return;
		}

		frm.add_custom_button(__("Send Test Notification"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the settings first so the test uses them."));
				return;
			}
			frm.call({ method: "send_test_notification", doc: frm.doc, freeze: true }).then((r) => {
				show_results(__("Test Notification"), r.message || [], (row) => [
					row.channel,
					row.target,
					row.ok ? __("Delivered") : __("Failed"),
					row.info,
				]);
			});
		});

		frm.add_custom_button(__("Run Health Checks Now"), () => {
			frm.call({ method: "run_health_checks_now", doc: frm.doc, freeze: true }).then((r) => {
				show_results(__("Health Checks"), r.message || [], (row) => [
					row.label,
					row.category,
					status_label(row.status),
					row.message,
				]);
				frm.reload_doc();
			});
		});

		frm.add_custom_button(
			__("Show Ingest Secret"),
			() => {
				frappe.confirm(__("The secret will be shown on screen and the viewing recorded. Continue?"), () => {
					frm.call({ method: "reveal_ingest_secret", doc: frm.doc }).then((r) => {
						frappe.msgprint({
							title: __("Ingest Secret"),
							message: `<code style="word-break:break-all">${frappe.utils.escape_html(r.message || "")}</code>`,
						});
					});
				});
			},
			__("Secrets")
		);

		[
			["ingest_secret", __("Rotate Ingest Secret"), __("Host agents stop reporting until they are given the new secret.")],
			["oci_notification_token", __("Rotate OCI Token"), __("OCI alarms stop arriving until the subscription URL is updated.")],
		].forEach(([fieldname, label, warning]) => {
			frm.add_custom_button(
				label,
				() => {
					frappe.confirm(warning + " " + __("Rotate now?"), () => {
						frm.call({ method: "rotate_secret", doc: frm.doc, args: { fieldname }, freeze: true }).then(() => {
							frappe.show_alert({ message: __("Rotated"), indicator: "green" });
							frm.reload_doc();
						});
					});
				},
				__("Secrets")
			);
		});
	},
});

function status_label(status) {
	return { ok: __("OK"), warning: __("Warning"), critical: __("Critical"), unknown: __("Not checked") }[status] || status;
}

function show_results(title, rows, cells) {
	const body = rows.length
		? rows
				.map(
					(row) =>
						"<tr>" + cells(row).map((c) => `<td>${frappe.utils.escape_html(String(c ?? ""))}</td>`).join("") + "</tr>"
				)
				.join("")
		: `<tr><td>${__("Nothing to report. Add channels or escalation contacts first.")}</td></tr>`;
	frappe.msgprint({
		title,
		wide: true,
		message: `<table class="table table-bordered table-condensed"><tbody>${body}</tbody></table>`,
	});
}

function render_integration_help(frm, is_ops) {
	const field = frm.get_field("integration_help");
	if (!field) {
		return;
	}
	if (!is_ops) {
		field.$wrapper.html("");
		return;
	}
	frm.call({ method: "get_integration_details", doc: frm.doc }).then((r) => {
		const d = r.message || {};
		const esc = frappe.utils.escape_html;
		field.$wrapper.html(`
			<div class="small text-muted" style="margin-bottom:12px">
				<p><strong>${__("Host agent endpoint")}</strong><br><code>${esc(d.ingest_url || "")}</code></p>
				<p>${__("Send JSON with headers X-H360-Timestamp (Unix seconds) and X-H360-Signature (hex HMAC-SHA256 of timestamp + '.' + body, keyed with the ingest secret). Requests older than 5 minutes or replayed are rejected.")}</p>
				<p><strong>${__("OCI Notifications HTTPS subscription URL")}</strong><br>
				<code style="word-break:break-all;white-space:normal">${esc(d.oci_url || __("Save the settings to generate a token."))}</code></p>
			</div>`);
	});
}
