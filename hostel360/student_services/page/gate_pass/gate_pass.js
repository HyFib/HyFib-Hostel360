// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.pages["gate-pass"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Gate Pass"),
		single_column: true,
	});
	wrapper.gate_pass = new Hostel360GatePass(page);
};

frappe.pages["gate-pass"].on_page_show = function (wrapper) {
	wrapper.gate_pass && wrapper.gate_pass.on_show();
};

const H360_GATE_API = "hostel360.student_services.page.gate_pass.gate_pass.";
const H360_GATE_REFRESH_MS = 60 * 1000;
const H360_GATE_MIN_SEARCH = 2;

// Replaces a student photo that failed to load with the student's initials.
function show_initials(img) {
	const $img = $(img);
	const $initials = $('<div class="h360-gate-avatar h360-gate-initials" aria-hidden="true"></div>')
		.toggleClass("h360-gate-avatar-lg", $img.attr("data-large") === "1")
		.text($img.attr("data-initials") || "");
	$img.replaceWith($initials);
}

class Hostel360GatePass {
	constructor(page) {
		this.page = page;
		this.tab = "leaving";
		this.board = { leaving: [], away: [], hostels: [] };
		this.results = null; // search results; null while the board is shown
		this.cards = {};
		this.now = null;
		this.request_id = 0;
		this.last_refresh = 0;

		this.$root = $(`<div class="h360-gate">
			<form class="h360-gate-search" role="search" autocomplete="off">
				<input type="search" class="form-control h360-gate-input" spellcheck="false"
					enterkeyhint="search" maxlength="60"
					placeholder="${__("Register number, name or room")}"
					aria-label="${__("Find a student by register number, name or room")}">
				<button type="button" class="btn btn-default h360-gate-clear" hidden>${__("Clear")}</button>
			</form>
			<div class="h360-gate-meta text-muted"></div>
			<div class="h360-gate-tabs" role="tablist"></div>
			<div class="h360-gate-list" aria-live="polite"></div>
		</div>`).appendTo(page.body);
		this.$input = this.$root.find(".h360-gate-input");
		this.$clear = this.$root.find(".h360-gate-clear");
		this.$meta = this.$root.find(".h360-gate-meta");
		this.$tabs = this.$root.find(".h360-gate-tabs");
		this.$list = this.$root.find(".h360-gate-list");

		page.set_secondary_action(__("Refresh"), () => this.refresh(), "refresh");
		this.bind_events();
		this.refresh();
		this.focus_search();

		// The board changes with the clock (check-out opens, a return becomes overdue).
		setInterval(() => {
			if (this.is_visible() && document.visibilityState === "visible") this.refresh(true);
		}, H360_GATE_REFRESH_MS);
	}

	bind_events() {
		const search_soon = frappe.utils.debounce(() => this.search(), 250);
		this.$input.on("input", () => {
			this.$clear.prop("hidden", !this.$input.val());
			search_soon();
		});
		this.$input.on("keydown", (e) => {
			if (e.key === "Escape") {
				e.preventDefault();
				this.clear_search();
			}
		});
		// A barcode reader on the ID card types the register number and presses Enter.
		this.$root.find(".h360-gate-search").on("submit", (e) => {
			e.preventDefault();
			this.search();
		});
		this.$clear.on("click", () => this.clear_search());
		this.$root.on("click", "[data-tab]", (e) => {
			this.tab = $(e.currentTarget).attr("data-tab");
			this.render();
		});
		this.$root.on("click", "[data-action]", (e) => {
			const $card = $(e.currentTarget).closest(".h360-gate-card");
			const card = this.cards[$card.attr("data-key")];
			card && this.confirm(card, $(e.currentTarget).attr("data-action"));
		});

		// Another gate or a warden may record a movement; keep this tablet in step.
		this.refresh_soon = frappe.utils.debounce(() => this.refresh(true), 1500);
		frappe.realtime.on("list_update", (data) => {
			if (data?.doctype === "Home Visit Leave" && this.is_visible()) this.refresh_soon();
		});
	}

	is_visible() {
		return frappe.get_route()[0] === "gate-pass";
	}

	on_show() {
		// The first show follows the load, which has just fetched the board.
		if (Date.now() - this.last_refresh > 5000) this.refresh(true);
		this.focus_search();
	}

	focus_search() {
		// On a touch screen focusing would pop the keyboard over the board; leave it to a tap.
		if (!("ontouchstart" in window)) this.$input.trigger("focus");
	}

	query() {
		return (this.$input.val() || "").trim();
	}

	clear_search() {
		this.$input.val("");
		this.$clear.prop("hidden", true);
		this.results = null;
		this.render();
		this.$input.trigger("focus");
	}

	refresh(quiet) {
		this.last_refresh = Date.now();
		if (this.query().length >= H360_GATE_MIN_SEARCH) return this.search(quiet);
		if (!quiet) this.loading();
		const id = ++this.request_id;
		return frappe.xcall(H360_GATE_API + "get_board").then((data) => {
			if (id !== this.request_id) return;
			this.board = data;
			this.now = data.now;
			this.results = null;
			this.render();
		});
	}

	search(quiet) {
		const query = this.query();
		if (query.length < H360_GATE_MIN_SEARCH) {
			if (this.results !== null) {
				this.results = null;
				this.render();
			}
			return Promise.resolve();
		}
		if (!quiet) this.loading();
		const id = ++this.request_id;
		return frappe.xcall(H360_GATE_API + "search", { query }).then((data) => {
			if (id !== this.request_id) return;
			this.now = data.now;
			this.results = data.results;
			this.render();
			// One match from a typed or scanned number: its button is ready for Enter or a tap.
			if (data.results.length === 1) this.$list.find("[data-action]").first().trigger("focus");
		});
	}

	loading() {
		this.$list.html(`<div class="h360-gate-empty text-muted">${__("Loading...")}</div>`);
	}

	// ------------------------------------------------------------------ rendering

	render() {
		this.cards = {};
		this.render_meta();
		if (this.results !== null) {
			this.$tabs.empty().prop("hidden", true);
			this.render_cards(
				this.results,
				__("No student in your hostels matches {0}.", [frappe.utils.escape_html(this.query())])
			);
			return;
		}
		const tab = (key, label, count) => `
			<button type="button" role="tab" class="h360-gate-tab ${this.tab === key ? "active" : ""}"
				data-tab="${key}" aria-selected="${this.tab === key}">
				${frappe.utils.escape_html(label)} <span class="h360-gate-count">${count}</span>
			</button>`;
		this.$tabs
			.prop("hidden", false)
			.html(
				tab("leaving", __("Leaving today"), this.board.leaving.length) +
					tab("away", __("Away"), this.board.away.length)
			);
		const empty =
			this.tab === "leaving"
				? __("No approved leave starts today.")
				: __("Nobody is away on leave right now.");
		const rows = this.board[this.tab] || [];
		this.render_cards(rows, empty);
		if (rows.length >= this.board.limit) {
			this.$list.append(
				`<div class="h360-gate-more text-muted">${__(
					"Showing the first {0}. Search to find a student who is not listed.",
					[this.board.limit]
				)}</div>`
			);
		}
	}

	render_meta() {
		const hostels = this.board.hostels || [];
		const where = hostels.length ? __("Hostels: {0}", [hostels.join(", ")]) : __("All hostels");
		const at = this.now ? __("updated {0}", [this.moment(this.now).format("HH:mm")]) : "";
		this.$meta.text([where, at].filter(Boolean).join(" · "));
	}

	render_cards(cards, empty_message) {
		if (!cards.length) {
			this.$list.html(`<div class="h360-gate-empty">${empty_message}</div>`);
			return;
		}
		this.$list.html(cards.map((card, index) => this.card_html(card, index)).join(""));
		this.$list.find("img.h360-gate-photo").on("error", function () {
			show_initials(this);
		});
	}

	card_html(card, index) {
		const e = frappe.utils.escape_html;
		const key = `${card.student}:${card.leave?.name || index}`;
		this.cards[key] = card;
		const leave = card.leave;
		const sub = [card.student, card.room_number && __("Room {0}", [card.room_number]), card.hostel]
			.filter(Boolean)
			.map(e)
			.join(" · ");

		let details = "";
		let action = "";
		if (leave) {
			details = `<div class="h360-gate-leave">${this.status_pill(leave)}
				<span>${e(this.when(leave.from_datetime))} &rarr; ${e(this.when(leave.to_datetime))}</span>
				${leave.destination ? `<span class="text-muted">· ${e(leave.destination)}</span>` : ""}</div>`;
			if (leave.checked_out_on) {
				details += `<div class="h360-gate-line text-muted">${e(
					__("Left {0}", [this.when(leave.checked_out_on)])
				)}</div>`;
			}
			if (leave.action === "check_out") {
				action = `<button type="button" class="btn btn-primary h360-gate-btn" data-action="check_out">
					${__("Check Out")}</button>`;
			} else if (leave.action === "return") {
				action = `<button type="button" class="btn btn-success h360-gate-btn" data-action="return">
					${__("Mark Returned")}</button>`;
			}
		} else {
			details = `<div class="h360-gate-leave"><span class="indicator-pill gray">${__(
				"No approved leave today"
			)}</span></div>`;
			if (card.upcoming) {
				details += `<div class="h360-gate-line text-muted">${e(
					__("Next approved leave: {0} to {1}", [
						this.when(card.upcoming.from_datetime),
						this.when(card.upcoming.to_datetime),
					])
				)}</div>`;
			}
		}
		const note = leave?.note ? `<div class="h360-gate-note">${e(leave.note)}</div>` : "";
		const state = !leave ? "none" : leave.status === "Overdue" ? "overdue" : leave.action ? "ready" : "waiting";
		return `<div class="h360-gate-card h360-gate-${state}" data-key="${e(key)}">
			${this.avatar(card)}
			<div class="h360-gate-info">
				<div class="h360-gate-name">${e(card.student_name)}</div>
				<div class="h360-gate-sub text-muted">${sub}</div>
				${details}
				${note}
			</div>
			${action ? `<div class="h360-gate-act">${action}</div>` : ""}
		</div>`;
	}

	avatar(card, large) {
		const e = frappe.utils.escape_html;
		const size = large ? "h360-gate-avatar-lg" : "";
		const initials = frappe.get_abbr(card.student_name || card.student, 2);
		if (!card.has_photo) {
			return `<div class="h360-gate-avatar h360-gate-initials ${size}" aria-hidden="true">${e(initials)}</div>`;
		}
		const src = `/api/method/${H360_GATE_API}student_photo?student=${encodeURIComponent(card.student)}`;
		// The fallback keeps only plain values in data attributes; show_initials() builds the
		// element with text(), so nothing read back from the page is ever parsed as HTML.
		return `<img class="h360-gate-avatar h360-gate-photo ${size}" src="${src}" alt="${e(
			__("Photo of {0}", [card.student_name])
		)}" loading="lazy" data-initials="${e(initials)}" data-large="${large ? 1 : 0}">`;
	}

	status_pill(leave) {
		if (leave.status === "Overdue" || leave.overdue_hours) {
			const label = leave.overdue_hours
				? __("Overdue by {0} h", [leave.overdue_hours])
				: __("Overdue");
			return `<span class="indicator-pill red">${frappe.utils.escape_html(label)}</span>`;
		}
		if (leave.status === "Checked Out") {
			return `<span class="indicator-pill orange">${__("Away")}</span>`;
		}
		return `<span class="indicator-pill blue">${__("Approved")}</span>`;
	}

	moment(value) {
		return moment(value, frappe.defaultDatetimeFormat);
	}

	when(value) {
		if (!value) return "";
		const at = this.moment(value);
		const now = this.now ? this.moment(this.now) : moment();
		const time = at.format("HH:mm");
		if (at.isSame(now, "day")) return __("Today {0}", [time]);
		if (at.isSame(now.clone().add(1, "day"), "day")) return __("Tomorrow {0}", [time]);
		if (at.isSame(now.clone().subtract(1, "day"), "day")) return __("Yesterday {0}", [time]);
		return at.format("DD MMM, HH:mm");
	}

	// ------------------------------------------------------------------ actions

	confirm(card, action) {
		const e = frappe.utils.escape_html;
		const leave = card.leave;
		const checking_out = action === "check_out";
		const dialog = new frappe.ui.Dialog({
			title: checking_out ? __("Check Out") : __("Mark Returned"),
			fields: [{ fieldtype: "HTML", fieldname: "summary" }],
			primary_action_label: checking_out ? __("Confirm Check Out") : __("Confirm Return"),
			primary_action: () => {
				const $button = dialog.get_primary_btn().prop("disabled", true);
				this.record(card, action).then(
					() => dialog.hide(),
					() => $button.prop("disabled", false)
				);
			},
		});
		dialog.fields_dict.summary.$wrapper.html(`<div class="h360-gate-confirm">
			${this.avatar(card, true)}
			<div class="h360-gate-name">${e(card.student_name)}</div>
			<div class="text-muted">${[card.student, card.room_number && __("Room {0}", [card.room_number])]
				.filter(Boolean)
				.map(e)
				.join(" · ")}</div>
			<div class="h360-gate-leave">${e(this.when(leave.from_datetime))} &rarr; ${e(
			this.when(leave.to_datetime)
		)}${leave.destination ? ` · ${e(leave.destination)}` : ""}</div>
			<p class="h360-gate-question">${
				checking_out
					? __("Check that this is the student, then record that they are leaving now.")
					: __("Check that this is the student, then record that they are back now.")
			}</p>
		</div>`);
		dialog.$wrapper.find("img.h360-gate-photo").on("error", function () {
			show_initials(this);
		});
		dialog.show();
	}

	record(card, action) {
		return frappe
			.xcall(H360_GATE_API + "record_movement", { leave: card.leave.name, action })
			.then(() => {
				frappe.show_alert(
					{
						message:
							action === "check_out"
								? __("{0} checked out", [frappe.utils.escape_html(card.student_name)])
								: __("{0} is back", [frappe.utils.escape_html(card.student_name)]),
						indicator: "green",
					},
					5
				);
				this.refresh(true);
			});
	}
}
