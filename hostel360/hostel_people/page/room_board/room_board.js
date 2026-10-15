// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.pages["room-board"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Room Board"),
		single_column: true,
	});
	wrapper.room_board = new RoomBoard(page, wrapper);
};

frappe.pages["room-board"].on_page_show = function (wrapper) {
	const board = wrapper.room_board;
	if (!board) return;
	if (frappe.route_options) {
		board.apply_route_options(frappe.route_options);
		frappe.route_options = null;
	}
	board.refresh();
};

const H360_BED_STATUSES = ["Vacant", "Occupied", "Reserved", "Maintenance"];
const H360_ROOM_TYPES = ["Single", "Double", "Triple", "Four Sharing", "Dormitory"];
const H360_METHOD = "hostel360.hostel_people.page.room_board.room_board.";

class RoomBoard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.data = null;
		this.filters = { status: "", room_type: "", search: "" };
		this.make_filters();
		this.make_layout();
		this.page.set_primary_action(__("Refresh"), () => this.refresh(), "refresh");
		this.page.add_inner_button(__("Occupancy Report"), () =>
			frappe.set_route("query-report", "Hostel Occupancy", {
				hostel: this.hostel_field.get_value() || undefined,
			})
		);
		this.page.add_inner_button(__("Allotments"), () => frappe.set_route("List", "Room Allotment"));

		const default_hostel = frappe.defaults.get_user_default("Hostel");
		if (default_hostel) this.hostel_field.set_value(default_hostel);
	}

	// ------------------------------------------------------------------ layout

	make_filters() {
		this.hostel_field = this.page.add_field({
			fieldname: "hostel",
			label: __("Hostel"),
			fieldtype: "Link",
			options: "Hostel",
			get_query: () => ({ filters: { is_active: 1 } }),
			change: () => {
				// Block and floor names start with the hostel code, e.g. BH1-A and BH1-A-F2.
				const hostel = this.hostel_field.get_value();
				const block = this.block_field.get_value();
				if (block && (!hostel || !block.startsWith(`${hostel}-`))) {
					this.block_field.set_value("");
					this.floor_field.set_value("");
				}
				this.schedule_refresh();
			},
		});
		this.block_field = this.page.add_field({
			fieldname: "block",
			label: __("Block"),
			fieldtype: "Link",
			options: "Hostel Block",
			get_query: () => ({ filters: { hostel: this.hostel_field.get_value() || "" } }),
			change: () => {
				const block = this.block_field.get_value();
				const floor = this.floor_field.get_value();
				if (floor && (!block || !floor.startsWith(`${block}-`))) this.floor_field.set_value("");
				this.schedule_refresh();
			},
		});
		this.floor_field = this.page.add_field({
			fieldname: "floor",
			label: __("Floor"),
			fieldtype: "Link",
			options: "Hostel Floor",
			get_query: () => {
				const filters = {};
				if (this.hostel_field.get_value()) filters.hostel = this.hostel_field.get_value();
				if (this.block_field.get_value()) filters.block = this.block_field.get_value();
				return { filters };
			},
			change: () => this.schedule_refresh(),
		});
		this.status_field = this.page.add_field({
			fieldname: "bed_status",
			label: __("Bed Status"),
			fieldtype: "Select",
			options: [{ value: "", label: __("All Beds") }].concat(
				H360_BED_STATUSES.map((s) => ({ value: s, label: __(s) }))
			),
			change: () => {
				this.filters.status = this.status_field.get_value();
				this.render();
			},
		});
		this.room_type_field = this.page.add_field({
			fieldname: "room_type",
			label: __("Room Type"),
			fieldtype: "Select",
			options: [{ value: "", label: __("All Room Types") }].concat(
				H360_ROOM_TYPES.map((s) => ({ value: s, label: __(s) }))
			),
			change: () => {
				this.filters.room_type = this.room_type_field.get_value();
				this.render();
			},
		});
		this.search_field = this.page.add_field({
			fieldname: "search",
			label: __("Room, student or register no."),
			fieldtype: "Data",
			change: frappe.utils.debounce(() => {
				this.filters.search = (this.search_field.get_value() || "").trim().toLowerCase();
				this.render();
			}, 200),
		});
	}

	make_layout() {
		this.$body = $(`
			<div class="h360-board">
				<div class="h360-board-summary"></div>
				<div class="h360-board-legend">
					${H360_BED_STATUSES.map(
						(s) =>
							`<span class="h360-legend-item"><span class="h360-swatch h360-bed-${s.toLowerCase()}"></span>${__(s)}</span>`
					).join("")}
				</div>
				<div class="h360-board-content"></div>
			</div>
		`).appendTo(this.page.main);
		this.$summary = this.$body.find(".h360-board-summary");
		this.$content = this.$body.find(".h360-board-content");

		this.$summary.on("click", "[data-status]", (e) => {
			const status = $(e.currentTarget).attr("data-status");
			this.status_field.set_value(this.filters.status === status ? "" : status);
		});
		this.$content.on("click", ".h360-bed", (e) => {
			const bed_name = $(e.currentTarget).attr("data-bed");
			this.open_bed(bed_name);
		});
	}

	apply_route_options(options) {
		if (options.hostel) this.hostel_field.set_value(options.hostel);
		if (options.block) this.block_field.set_value(options.block);
		if (options.floor) this.floor_field.set_value(options.floor);
		if (options.status) this.status_field.set_value(options.status);
	}

	// ------------------------------------------------------------------ data

	schedule_refresh() {
		clearTimeout(this.refresh_timer);
		this.refresh_timer = setTimeout(() => this.refresh(), 150);
	}

	refresh() {
		if (this.loading) {
			// A filter changed while loading; load again with the latest filters afterwards.
			this.reload_after = true;
			return this.loading;
		}
		const scroll = window.scrollY;
		this.loading = frappe
			.xcall(H360_METHOD + "get_board", {
				hostel: this.hostel_field.get_value() || null,
				block: this.block_field.get_value() || null,
				floor: this.floor_field.get_value() || null,
			})
			.then((data) => {
				this.data = data;
				this.index_beds();
				this.render();
				window.scrollTo(0, scroll);
			})
			.finally(() => {
				this.loading = null;
				if (this.reload_after) {
					this.reload_after = false;
					this.refresh();
				}
			});
		return this.loading;
	}

	index_beds() {
		this.beds = {};
		for (const hostel of this.data.hostels) {
			for (const block of hostel.blocks) {
				for (const floor of block.floors) {
					for (const room of floor.rooms) {
						for (const bed of room.beds) {
							this.beds[bed.name] = { bed, room, floor, block, hostel };
						}
					}
				}
			}
		}
	}

	bed_matches(bed, room) {
		if (this.filters.status && bed.status !== this.filters.status) return false;
		const q = this.filters.search;
		if (!q) return true;
		return [room.room_number, room.name, bed.name, bed.student_name, bed.register_number]
			.filter(Boolean)
			.some((value) => String(value).toLowerCase().includes(q));
	}

	// ------------------------------------------------------------------ rendering

	render() {
		if (!this.data) return;
		this.render_summary();

		const esc = frappe.utils.escape_html;
		const filtering = this.filters.status || this.filters.search || this.filters.room_type;
		const html = [];
		let shown_rooms = 0;

		for (const hostel of this.data.hostels) {
			const blocks_html = [];
			for (const block of hostel.blocks) {
				const floors_html = [];
				for (const floor of block.floors) {
					const rooms_html = [];
					for (const room of floor.rooms) {
						if (this.filters.room_type && room.room_type !== this.filters.room_type) continue;
						const beds = room.beds.filter((bed) => this.bed_matches(bed, room));
						if (filtering && !beds.length) continue;
						shown_rooms++;
						rooms_html.push(this.room_html(room, beds, filtering));
					}
					if (rooms_html.length) {
						floors_html.push(`
							<div class="h360-floor">
								<div class="h360-floor-title">${esc(__(floor.floor_label))}</div>
								<div class="h360-rooms">${rooms_html.join("")}</div>
							</div>`);
					}
				}
				if (floors_html.length) {
					blocks_html.push(`
						<div class="h360-block">
							<div class="h360-block-title">${esc(block.block_name)}</div>
							${floors_html.join("")}
						</div>`);
				}
			}
			if (!blocks_html.length) continue;
			const percent = hostel.total ? Math.round((hostel.occupied * 100) / hostel.total) : 0;
			html.push(`
				<section class="h360-hostel">
					<header class="h360-hostel-head">
						<div>
							<h4 class="h360-hostel-name">${esc(hostel.hostel_name)}</h4>
							<span class="indicator-pill ${hostel.gender === "Female" ? "pink" : hostel.gender === "Male" ? "blue" : "purple"}">${esc(__(hostel.gender))}</span>
						</div>
						<div class="h360-hostel-stats">
							<span>${__("Beds")}: <b>${hostel.total || 0}</b></span>
							<span>${__("Occupied")}: <b>${hostel.occupied || 0}</b></span>
							<span>${__("Vacant")}: <b>${hostel.vacant || 0}</b></span>
							<span class="h360-progress" title="${__("Occupancy")} ${percent}%">
								<span style="width:${percent}%"></span>
							</span>
							<span>${percent}%</span>
						</div>
					</header>
					${blocks_html.join("")}
				</section>`);
		}

		if (!html.length) {
			this.$content.html(this.empty_state(filtering));
			return;
		}
		this.$content.html(html.join(""));
		this.page.set_indicator(__("{0} rooms", [shown_rooms]), "blue");
	}

	room_html(room, beds, filtering) {
		const esc = frappe.utils.escape_html;
		const flagged = ["Under Maintenance", "Blocked"].includes(room.status);
		const bed_buttons = (filtering ? beds : room.beds)
			.map((bed) => {
				const who = bed.student_name
					? `${bed.student_name}${bed.register_number ? " · " + bed.register_number : ""}`
					: __(bed.status);
				const title = `${__("Bed {0}", [bed.label])}: ${who}`;
				return `<button type="button" class="h360-bed h360-bed-${bed.status.toLowerCase()}"
						data-bed="${esc(bed.name)}" title="${esc(title)}" aria-label="${esc(title)}">
						<span class="h360-bed-label">${esc(bed.label)}</span>
						${bed.student_name ? `<span class="h360-bed-name">${esc(this.short_name(bed.student_name))}</span>` : ""}
					</button>`;
			})
			.join("");
		return `
			<div class="h360-room ${flagged ? "h360-room-flagged" : ""}">
				<div class="h360-room-head">
					<a class="h360-room-number" href="${frappe.utils.get_form_link("Hostel Room", room.name)}">${esc(room.room_number)}</a>
					<span class="h360-room-meta">${esc(__(room.room_type))} · ${room.occupied || 0}/${room.capacity || 0}</span>
				</div>
				${flagged ? `<div class="h360-room-status">${esc(__(room.status))}</div>` : ""}
				<div class="h360-beds">${bed_buttons}</div>
			</div>`;
	}

	render_summary() {
		const s = this.data.summary;
		const percent = s.total ? Math.round(((s.Occupied || 0) * 100) / s.total) : 0;
		const chip = (status, label, value) => `
			<button type="button" class="h360-summary-chip ${this.filters.status === status ? "active" : ""}"
				data-status="${status}">
				<span class="h360-summary-value">${value || 0}</span>
				<span class="h360-summary-label">${label}</span>
			</button>`;
		this.$summary.html(`
			<div class="h360-summary-chip static">
				<span class="h360-summary-value">${s.total || 0}</span>
				<span class="h360-summary-label">${__("Beds")} · ${percent}% ${__("occupied")}</span>
			</div>
			${H360_BED_STATUSES.map((status) => chip(status, __(status), s[status])).join("")}
		`);
	}

	empty_state(filtering) {
		const message = filtering
			? __("No beds match these filters.")
			: this.data.permitted_hostels.length
			? __("No rooms yet. Add rooms to a hostel and their beds appear here.")
			: __("You do not have access to any hostel yet.");
		return `<div class="h360-empty text-muted">${message}</div>`;
	}

	short_name(name) {
		const parts = String(name).split(" ").filter(Boolean);
		if (parts.length <= 1) return name.slice(0, 12);
		return `${parts[0].slice(0, 10)} ${parts[parts.length - 1][0]}.`;
	}

	// ------------------------------------------------------------------ bed actions

	open_bed(bed_name) {
		const entry = this.beds[bed_name];
		if (!entry) return;
		const { bed, room, hostel } = entry;
		const perms = this.data.permissions;
		const esc = frappe.utils.escape_html;

		const dialog = new frappe.ui.Dialog({
			title: __("{0} · Room {1} · Bed {2}", [hostel.hostel_name, room.room_number, bed.label]),
			fields: [{ fieldtype: "HTML", fieldname: "details" }],
		});

		const details = [
			`<p><span class="indicator-pill ${this.status_colour(bed.status)}">${esc(__(bed.status))}</span>
			 <span class="text-muted">${esc(__(room.room_type))} · ${room.occupied || 0}/${room.capacity || 0} ${__("occupied")}</span></p>`,
		];
		if (bed.student_name) {
			details.push(`<p><b>${esc(bed.student_name)}</b><br>
				<span class="text-muted">${esc(bed.register_number || "")}</span></p>`);
		}
		dialog.fields_dict.details.$wrapper.html(details.join(""));

		const actions = [];
		const add = (label, handler, primary = false) => actions.push({ label, handler, primary });

		if (bed.status === "Occupied" && bed.allotment) {
			if (perms.allot) {
				add(__("Transfer"), () => this.transfer_dialog(entry), true);
				add(__("Vacate"), () => this.vacate_dialog(entry));
			}
			add(__("Open Allotment"), () => frappe.set_route("Form", "Room Allotment", bed.allotment));
			if (bed.student) add(__("Open Student"), () => frappe.set_route("Form", "Hostel Student", bed.student));
		} else if (bed.status === "Vacant") {
			if (perms.allot && !["Under Maintenance", "Blocked"].includes(room.status)) {
				add(__("Allot"), () => this.allot_dialog(entry), true);
			}
			if (perms.edit_beds) {
				add(__("Reserve"), () => this.set_bed_status(bed, "Reserved"));
				add(__("Maintenance"), () => this.set_bed_status(bed, "Maintenance"));
			}
		} else if (perms.edit_beds) {
			add(__("Mark Vacant"), () => this.set_bed_status(bed, "Vacant"), true);
		}
		add(__("Open Bed"), () => frappe.set_route("Form", "Hostel Bed", bed.name));

		const $actions = $(`<div class="h360-bed-actions"></div>`).appendTo(
			dialog.fields_dict.details.$wrapper
		);
		for (const action of actions) {
			$(`<button type="button" class="btn btn-sm ${action.primary ? "btn-primary" : "btn-default"}"></button>`)
				.text(action.label)
				.on("click", () => {
					dialog.hide();
					action.handler();
				})
				.appendTo($actions);
		}
		dialog.show();
	}

	status_colour(status) {
		return { Vacant: "green", Occupied: "blue", Reserved: "orange", Maintenance: "gray" }[status] || "gray";
	}

	allot_dialog({ bed, room, hostel }) {
		const student_filters = {
			status: ["in", ["Active", "Applied"]],
			validation_status: ["!=", "Rejected"],
			current_allotment: ["is", "not set"],
		};
		if (hostel.gender === "Male" || hostel.gender === "Female") student_filters.gender = hostel.gender;

		const dialog = new frappe.ui.Dialog({
			title: __("Allot Room {0} · Bed {1}", [room.room_number, bed.label]),
			fields: [
				{
					fieldname: "student",
					fieldtype: "Link",
					label: __("Student"),
					options: "Hostel Student",
					reqd: 1,
					get_query: () => ({
						query: "hostel360.hostel_people.queries.allottable_student_query",
						filters: student_filters,
					}),
					description: __(
						"Students without a bed whose gender matches this hostel. For a student returning from another hostel, type the exact register or mobile number."
					),
				},
				{
					fieldname: "from_date",
					fieldtype: "Date",
					label: __("From Date"),
					default: frappe.datetime.get_today(),
					reqd: 1,
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "to_date",
					fieldtype: "Date",
					label: __("To Date"),
					description: __("Optional planned end of stay."),
				},
				{ fieldtype: "Section Break" },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			],
			primary_action_label: __("Allot"),
			primary_action: (values) => {
				frappe
					.xcall(H360_METHOD + "allot", { bed: bed.name, ...values })
					.then((name) => {
						dialog.hide();
						frappe.show_alert({
							message: __("Allotted. Allotment {0} created.", [name]),
							indicator: "green",
						});
						this.refresh();
					});
			},
		});
		dialog.show();
	}

	transfer_dialog({ bed }) {
		const dialog = new frappe.ui.Dialog({
			title: __("Transfer {0}", [bed.student_name || bed.student]),
			fields: [
				{
					fieldname: "hostel",
					fieldtype: "Link",
					label: __("Hostel"),
					options: "Hostel",
					default: this.beds[bed.name].hostel.name,
					get_query: () => ({ filters: { is_active: 1 } }),
				},
				{
					fieldname: "new_bed",
					fieldtype: "Link",
					label: __("New Bed"),
					options: "Hostel Bed",
					reqd: 1,
					get_query: () => ({
						query: H360_METHOD + "search_vacant_beds",
						filters: {
							hostel: dialog.get_value("hostel"),
							student: bed.student,
							exclude: bed.name,
						},
					}),
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "from_date",
					fieldtype: "Date",
					label: __("Transfer Date"),
					default: frappe.datetime.get_today(),
					reqd: 1,
				},
				{ fieldtype: "Section Break" },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			],
			primary_action_label: __("Transfer"),
			primary_action: (values) => {
				frappe
					.xcall(H360_METHOD + "transfer", {
						allotment: bed.allotment,
						new_bed: values.new_bed,
						from_date: values.from_date,
						remarks: values.remarks,
					})
					.then((name) => {
						dialog.hide();
						frappe.show_alert({
							message: __("Transferred. New allotment {0}.", [name]),
							indicator: "green",
						});
						this.refresh();
					});
			},
		});
		dialog.show();
	}

	vacate_dialog({ bed }) {
		const dialog = new frappe.ui.Dialog({
			title: __("Vacate {0}", [bed.student_name || bed.student]),
			fields: [
				{
					fieldname: "vacated_on",
					fieldtype: "Date",
					label: __("Vacated On"),
					default: frappe.datetime.get_today(),
					reqd: 1,
				},
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason"),
					reqd: 1,
					description: __("For example: course completed, discontinued, moved to day scholar."),
				},
			],
			primary_action_label: __("Vacate"),
			primary_action: (values) => {
				frappe.xcall(H360_METHOD + "vacate", { allotment: bed.allotment, ...values }).then(() => {
					dialog.hide();
					frappe.show_alert({ message: __("Bed released"), indicator: "green" });
					this.refresh();
				});
			},
		});
		dialog.show();
	}

	set_bed_status(bed, status) {
		frappe.xcall(H360_METHOD + "set_bed_status", { bed: bed.name, status }).then(() => {
			frappe.show_alert({ message: __("Bed {0} is now {1}", [bed.label, __(status)]), indicator: "green" });
			this.refresh();
		});
	}
}
