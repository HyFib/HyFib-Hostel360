// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.pages["ops-status"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Operations Status"),
		single_column: true,
	});
	wrapper.ops_status = new OpsStatusPage(page);
};

frappe.pages["ops-status"].on_page_show = function (wrapper) {
	wrapper.ops_status?.refresh();
};

const OPS_STATUS_COLOURS = { ok: "green", warning: "orange", critical: "red", unknown: "gray" };
const OPS_STATUS_LABELS = {
	ok: __("Healthy"),
	warning: __("Warning"),
	critical: __("Critical"),
	unknown: __("Not checked"),
};
const OPS_TEXT_CLASSES = { red: "danger", orange: "warning", green: "success" };
const OPS_REFRESH_SECONDS = 60;

class OpsStatusPage {
	constructor(page) {
		this.page = page;
		this.$body = $(`<div class="ops-status-page"></div>`).appendTo(page.main);
		this.inject_style();

		page.set_primary_action(__("Refresh"), () => this.refresh(), "refresh");
		page.add_menu_item(__("Alert History"), () => frappe.set_route("query-report", "Alert History"));
		page.add_menu_item(__("Uptime and Incidents"), () => frappe.set_route("query-report", "Uptime and Incidents"));
		page.add_menu_item(__("Support Tickets"), () => frappe.set_route("List", "Support Ticket"));
		page.add_menu_item(__("Monthly Performance Reports"), () =>
			frappe.set_route("List", "Monthly Performance Report")
		);
		if (frappe.user.has_role(["HyFib Operations", "System Manager"])) {
			page.add_menu_item(__("Alert Settings"), () => frappe.set_route("Form", "Ops Alert Settings"));
		}

		this.timer = setInterval(() => {
			if (frappe.get_route_str() === "ops-status" && !document.hidden) {
				this.refresh(true);
			}
		}, OPS_REFRESH_SECONDS * 1000);
	}

	refresh(quiet = false) {
		if (this.loading) {
			return;
		}
		this.loading = true;
		if (!quiet) {
			this.$body.html(`<div class="text-muted ops-status-loading">${__("Checking components...")}</div>`);
		}
		frappe
			.call({ method: "hostel360.api.health.status", type: "GET" })
			.then((r) => this.render(r.message || {}))
			.catch(() => {
				this.$body.html(
					`<div class="alert alert-danger">${__("Could not load the status. Try again in a minute.")}</div>`
				);
			})
			.finally(() => {
				this.loading = false;
			});
	}

	render(data) {
		const esc = frappe.utils.escape_html;
		const overall = data.status || "unknown";
		this.page.set_indicator(OPS_STATUS_LABELS[overall], OPS_STATUS_COLOURS[overall]);

		const open = data.open_alerts || {};
		const backup = data.last_backup;
		const cards = [
			this.card(__("Critical alerts"), open.Critical || 0, open.Critical ? "red" : "green", {
				status: ["in", ["Open", "Acknowledged"]],
				severity: "Critical",
			}),
			this.card(__("Warnings"), open.Warning || 0, open.Warning ? "orange" : "green", {
				status: ["in", ["Open", "Acknowledged"]],
				severity: "Warning",
			}),
			this.card(
				__("Last backup"),
				backup ? frappe.datetime.comment_when(backup.finished_at) : __("None recorded"),
				backup ? "blue" : "red"
			),
			this.card(
				__("Last health check"),
				data.last_health_check_on ? frappe.datetime.comment_when(data.last_health_check_on) : __("Never"),
				data.last_health_check_on ? "blue" : "orange"
			),
		].join("");

		const rows = (data.components || [])
			.map((c) => {
				const colour = OPS_STATUS_COLOURS[c.status] || "gray";
				return `<tr>
					<td><span class="indicator-pill ${colour}">${esc(OPS_STATUS_LABELS[c.status] || c.status)}</span></td>
					<td>${esc(c.label)}${c.core ? ` <span class="text-muted small">${__("core")}</span>` : ""}</td>
					<td class="text-muted">${esc(__(c.category))}</td>
					<td>${esc(c.message || "")}</td>
				</tr>`;
			})
			.join("");

		this.$body.html(`
			<div class="ops-status-meta text-muted">
				${esc(data.instance_label || "")} &middot; ${esc(__(data.environment || ""))} &middot;
				${__("checked {0}", [frappe.datetime.comment_when(data.checked_at)])}
			</div>
			<div class="ops-status-cards">${cards}</div>
			<div class="frappe-card ops-status-table">
				<table class="table table-hover">
					<thead><tr>
						<th style="width:130px">${__("Status")}</th>
						<th>${__("Component")}</th>
						<th style="width:140px">${__("Area")}</th>
						<th>${__("Details")}</th>
					</tr></thead>
					<tbody>${rows}</tbody>
				</table>
			</div>`);

		this.$body.find("[data-alert-filters]").on("click", (e) => {
			const filters = JSON.parse(decodeURIComponent($(e.currentTarget).attr("data-alert-filters")));
			frappe.set_route("List", "Ops Alert", filters);
		});
	}

	card(label, value, colour, filters) {
		const esc = frappe.utils.escape_html;
		const link = filters ? `data-alert-filters="${encodeURIComponent(JSON.stringify(filters))}" role="button"` : "";
		return `<div class="frappe-card ops-status-card ${filters ? "ops-status-link" : ""}" ${link}>
			<div class="ops-status-card-label">${esc(label)}</div>
			<div class="ops-status-card-value text-${OPS_TEXT_CLASSES[colour] || "primary"}">${esc(String(value))}</div>
		</div>`;
	}

	inject_style() {
		if (document.getElementById("ops-status-style")) {
			return;
		}
		$(`<style id="ops-status-style">
			.ops-status-page { padding: var(--padding-md); }
			.ops-status-meta { margin-bottom: var(--margin-md); }
			.ops-status-loading { padding: var(--padding-lg); }
			.ops-status-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: var(--margin-md); margin-bottom: var(--margin-lg); }
			.ops-status-card { padding: var(--padding-md); }
			.ops-status-link { cursor: pointer; }
			.ops-status-link:hover { box-shadow: var(--shadow-md); }
			.ops-status-card-label { color: var(--text-muted); font-size: var(--text-sm); }
			.ops-status-card-value { font-size: var(--text-2xl); font-weight: 600; margin-top: 4px; }
			.ops-status-table { overflow-x: auto; }
			.ops-status-table table { margin-bottom: 0; }
		</style>`).appendTo("head");
	}
}
