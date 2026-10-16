// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.pages["approvals"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Approvals"),
		single_column: true,
	});
	wrapper.approvals = new Hostel360Approvals(page);
};

frappe.pages["approvals"].on_page_show = function (wrapper) {
	wrapper.approvals && wrapper.approvals.refresh();
};

const H360_SECTIONS = [
	{ key: "leaves", label: __("Leave"), doctype: "Home Visit Leave", decide: true },
	{ key: "mess_reductions", label: __("Mess Reduction"), doctype: "Mess Reduction Application", decide: true },
	{ key: "certificates", label: __("Certificates"), doctype: "Bonafide Certificate Request", decide: true },
	{ key: "indents", label: __("Mess Indents"), doctype: "Mess Indent", decide: true },
	{ key: "grievances", label: __("Grievances"), doctype: "Hostel Grievance", decide: false },
];

class Hostel360Approvals {
	constructor(page) {
		this.page = page;
		this.active = "all";
		this.data = { sections: {}, counts: {} };
		this.$root = $(`<div class="h360-approvals">
			<div class="h360-tabs" role="tablist"></div>
			<div class="h360-list" aria-live="polite"></div>
		</div>`).appendTo(page.body);
		this.$tabs = this.$root.find(".h360-tabs");
		this.$list = this.$root.find(".h360-list");

		page.set_secondary_action(__("Refresh"), () => this.refresh(), "refresh");
		this.$root.on("click", "[data-tab]", (e) => this.select($(e.currentTarget).attr("data-tab")));
		this.$root.on("click", "[data-action]", (e) => this.handle_action($(e.currentTarget)));

		// Another approver may act on the same item; refresh quietly when the lists change.
		this.refresh_soon = frappe.utils.debounce(() => this.refresh(true), 1500);
		frappe.realtime.on("list_update", (data) => {
			if (H360_SECTIONS.some((s) => s.doctype === data?.doctype) && this.is_visible()) {
				this.refresh_soon();
			}
		});
		this.refresh();
	}

	is_visible() {
		return frappe.get_route()[0] === "approvals";
	}

	refresh(quiet) {
		if (!quiet) this.$list.html(`<div class="h360-empty text-muted">${__("Loading...")}</div>`);
		return frappe
			.xcall("hostel360.student_services.page.approvals.approvals.get_inbox")
			.then((data) => {
				this.data = data;
				this.render();
			});
	}

	sections() {
		return H360_SECTIONS.filter((s) => s.key in (this.data.sections || {}));
	}

	render() {
		const sections = this.sections();
		if (this.active !== "all" && !sections.some((s) => s.key === this.active)) this.active = "all";
		const total = sections.reduce((n, s) => n + (this.data.counts[s.key] || 0), 0);
		const tab = (key, label, count) => `
			<button type="button" role="tab" class="h360-tab ${this.active === key ? "active" : ""}"
				data-tab="${key}" aria-selected="${this.active === key}">
				${frappe.utils.escape_html(label)}
				<span class="h360-badge ${count ? "has-items" : ""}">${count}</span>
			</button>`;
		this.$tabs.html(
			tab("all", __("All"), total) +
				sections.map((s) => tab(s.key, s.label, this.data.counts[s.key] || 0)).join("")
		);
		this.render_list();
	}

	select(key) {
		this.active = key;
		this.render();
	}

	render_list() {
		const sections = this.sections().filter((s) => this.active === "all" || s.key === this.active);
		const cards = [];
		for (const section of sections) {
			for (const row of this.data.sections[section.key] || []) {
				cards.push(this.card(section, row));
			}
		}
		if (!cards.length) {
			this.$list.html(`<div class="h360-empty">
				<div class="h360-empty-title">${__("You're all caught up")}</div>
				<div class="text-muted">${__("Nothing is waiting for your decision.")}</div>
			</div>`);
			return;
		}
		const limited = sections.some((s) => (this.data.counts[s.key] || 0) >= this.data.limit);
		this.$list.html(
			cards.join("") +
				(limited
					? `<div class="text-muted small h360-more">${__(
							"Showing the oldest {0} items in each list. Decide these to see more.",
							[this.data.limit]
					  )}</div>`
					: "")
		);
	}

	card(section, row) {
		const e = frappe.utils.escape_html;
		const body = this[`body_${section.key}`](row);
		const open_link = frappe.utils.get_form_link(section.doctype, row.name);
		let actions = "";
		if (section.decide) {
			actions = `<textarea class="form-control h360-remarks" rows="1" maxlength="1000"
					placeholder="${__("Remarks (required to reject)")}" aria-label="${__("Remarks")}"></textarea>
				<div class="h360-buttons">
					<button type="button" class="btn btn-default btn-sm" data-action="reject">${__("Reject")}</button>
					<button type="button" class="btn btn-primary btn-sm" data-action="approve">${__("Approve")}</button>
				</div>`;
		} else if (this.data.can_act_on_grievances || row.assigned_to === frappe.session.user) {
			const take =
				row.assigned_to === frappe.session.user || !this.data.can_act_on_grievances
					? ""
					: `<button type="button" class="btn btn-default btn-sm" data-action="take">${__("Take Ownership")}</button>`;
			actions = `<textarea class="form-control h360-remarks" rows="1" maxlength="5000"
					placeholder="${__("What was done (required to resolve)")}" aria-label="${__("Resolution")}"></textarea>
				<div class="h360-buttons">
					${take}
					<button type="button" class="btn btn-primary btn-sm" data-action="resolve">${__("Resolve")}</button>
				</div>`;
		}
		return `<div class="h360-card" data-doctype="${e(section.doctype)}" data-name="${e(row.name)}" data-section="${section.key}">
			<div class="h360-card-head">
				<span class="h360-kind h360-kind-${section.key}">${e(section.label)}</span>
				<a class="h360-title" href="${open_link}">${body.title}</a>
				<span class="h360-when text-muted" title="${e(frappe.datetime.str_to_user(row.creation))}">
					${frappe.datetime.comment_when(row.creation, true)}
				</span>
			</div>
			<div class="h360-card-body">${body.lines.filter(Boolean).join("")}</div>
			<div class="h360-error text-danger small"></div>
			${actions ? `<div class="h360-card-actions">${actions}</div>` : ""}
		</div>`;
	}

	line(label, value) {
		if (value === undefined || value === null || value === "") return "";
		return `<div class="h360-line"><span class="text-muted">${frappe.utils.escape_html(label)}</span>
			<span>${frappe.utils.escape_html(String(value))}</span></div>`;
	}

	pill(text, colour) {
		return `<span class="indicator-pill ${colour}">${frappe.utils.escape_html(text)}</span>`;
	}

	student_title(row, extra) {
		const e = frappe.utils.escape_html;
		return `${e(row.student_name || row.student || "")}${extra ? ` <span class="text-muted">· ${e(extra)}</span>` : ""}`;
	}

	body_leaves(row) {
		const from = frappe.datetime.str_to_user(row.from_datetime);
		const to = frappe.datetime.str_to_user(row.to_datetime);
		const parent =
			row.parent_approval_status === "Approved"
				? this.pill(
						row.applied_by === "Parent" ? __("Applied by parent") : __("Parent approved"),
						"green"
				  )
				: row.parent_approval_status === "Not Required"
				? this.pill(__("Parent approval not required"), "gray")
				: "";
		const mess = row.apply_mess_reduction
			? this.pill(__("Mess reduction: {0} days", [row.mess_reduction_days || 0]), "blue")
			: "";
		return {
			title: this.student_title(row, row.room || row.hostel),
			lines: [
				`<div class="h360-line h360-strong">${frappe.utils.escape_html(from)} &rarr; ${frappe.utils.escape_html(to)}
					<span class="text-muted">(${__("{0} days", [row.number_of_days || 1])})</span></div>`,
				this.line(__("Destination"), row.destination),
				this.line(__("Reason"), row.reason),
				this.line(__("Contact"), row.contact_during_leave),
				this.line(__("Parent's remarks"), row.parent_remarks),
				parent || mess ? `<div class="h360-pills">${parent}${mess}</div>` : "",
			],
		};
	}

	body_mess_reductions(row) {
		return {
			title: this.student_title(row, row.hostel),
			lines: [
				`<div class="h360-line h360-strong">${frappe.datetime.str_to_user(row.from_date)} &rarr;
					${frappe.datetime.str_to_user(row.to_date)}
					<span class="text-muted">(${__("{0} days", [row.number_of_days || 0])})</span></div>`,
				this.line(__("Reason"), row.reason ? __(row.reason) : ""),
				this.line(__("Details"), row.description),
				this.line(__("Mess"), row.mess),
				row.home_visit_leave ? this.line(__("From leave"), row.home_visit_leave) : "",
			],
		};
	}

	body_certificates(row) {
		return {
			title: this.student_title(row, row.hostel),
			lines: [
				`<div class="h360-line h360-strong">${frappe.utils.escape_html(__("Bonafide certificate for {0}", [__(row.purpose)]))}</div>`,
				this.line(__("Details"), row.purpose_details),
				this.line(__("Programme"), [row.program, row.year_of_study && __("Year {0}", [row.year_of_study])].filter(Boolean).join(", ")),
			],
		};
	}

	body_indents(row) {
		return {
			title: frappe.utils.escape_html(`${row.name} · ${row.mess || ""}`),
			lines: [
				this.line(__("Required by"), row.required_by ? frappe.datetime.str_to_user(row.required_by) : ""),
				row.total_estimated_amount !== undefined
					? this.line(__("Estimated amount"), format_currency(row.total_estimated_amount))
					: "",
				this.line(
					__("Raised by"),
					row.requested_by || row.owner ? frappe.user.full_name(row.requested_by || row.owner) : ""
				),
				this.line(__("Remarks"), row.remarks),
			],
		};
	}

	body_grievances(row) {
		const pills = [
			row.status === "Escalated"
				? this.pill(__("Escalated (level {0})", [row.escalation_level || 1]), "red")
				: this.pill(__(row.status), "orange"),
			row.priority === "Urgent" ? this.pill(__("Urgent"), "red") : "",
			row.is_confidential ? this.pill(__("Confidential"), "purple") : "",
		].join("");
		return {
			title: frappe.utils.escape_html(row.subject || row.name),
			lines: [
				`<div class="h360-pills">${pills}</div>`,
				this.line(__("Category"), __(row.category)),
				this.line(__("Hostel"), row.hostel),
				this.line(__("Student"), row.is_confidential ? "" : row.student_name),
				this.line(__("Assigned to"), row.assigned_to ? frappe.user.full_name(row.assigned_to) : __("Nobody yet")),
				this.line(__("Action due by"), row.sla_due_by ? frappe.datetime.str_to_user(row.sla_due_by) : ""),
			],
		};
	}

	handle_action($button) {
		const $card = $button.closest(".h360-card");
		const action = $button.attr("data-action");
		const remarks = ($card.find(".h360-remarks").val() || "").trim();
		const $error = $card.find(".h360-error").text("");

		if ((action === "reject" || action === "resolve") && !remarks) {
			$error.text(action === "reject" ? __("Please enter a reason for rejecting.") : __("Please say what was done."));
			$card.find(".h360-remarks").trigger("focus");
			return;
		}

		const doctype = $card.attr("data-doctype");
		const name = $card.attr("data-name");
		const call =
			doctype === "Hostel Grievance"
				? frappe.xcall("hostel360.student_services.page.approvals.approvals.act_on_grievance", {
						name,
						action,
						// The box holds the resolution; taking ownership sends nothing with it.
						message: action === "resolve" ? remarks : null,
				  })
				: frappe.xcall("hostel360.student_services.page.approvals.approvals.decide", {
						doctype,
						name,
						action,
						remarks,
				  });

		$card.addClass("h360-busy").find("button, textarea").prop("disabled", true);
		call
			.then(() => {
				const messages = {
					approve: __("Approved"),
					reject: __("Rejected"),
					take: __("Assigned to you"),
					resolve: __("Resolved"),
				};
				frappe.show_alert({ message: `${messages[action]}: ${name}`, indicator: action === "reject" ? "orange" : "green" });
				if (action === "take") {
					// Keep a resolution typed before taking ownership for when the work is done.
					this.refresh(true).then(() => {
						if (!remarks) return;
						this.$list.find(`.h360-card[data-name="${CSS.escape(name)}"] .h360-remarks`).val(remarks);
					});
					return;
				}
				this.remove_item($card.attr("data-section"), name);
				$card.slideUp(150, () => this.render());
			})
			.catch(() => {
				$card.removeClass("h360-busy").find("button, textarea").prop("disabled", false);
			});
	}

	remove_item(section, name) {
		const rows = this.data.sections[section] || [];
		this.data.sections[section] = rows.filter((r) => r.name !== name);
		this.data.counts[section] = this.data.sections[section].length;
	}
}
