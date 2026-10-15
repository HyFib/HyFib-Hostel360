// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const H360_ROOM_TYPE_BEDS = { Single: 1, Double: 2, Triple: 3, "Four Sharing": 4 };

frappe.ui.form.on("Hostel Room", {
	setup(frm) {
		frm.set_query("block", () => ({ filters: { hostel: frm.doc.hostel || "" } }));
		frm.set_query("floor", () => {
			const filters = { hostel: frm.doc.hostel || "" };
			if (frm.doc.block) filters.block = frm.doc.block;
			return { filters };
		});
	},

	refresh(frm) {
		frm.trigger("render_beds");
		if (frm.is_new()) return;

		frm.add_custom_button(__("Room Board"), () =>
			frappe.set_route("room-board", { hostel: frm.doc.hostel, block: frm.doc.block || "" })
		);
		if (frm.doc.vacant_beds && !["Under Maintenance", "Blocked"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Allot a Bed"),
				() =>
					frappe.new_doc("Room Allotment", {
						hostel: frm.doc.hostel,
						room: frm.doc.name,
					}),
				__("Create")
			);
		}
		frm.add_custom_button(
			__("Allotment History"),
			() =>
				frappe.set_route("query-report", "Room Allotment History", {
					hostel: frm.doc.hostel,
					room: frm.doc.name,
				}),
			__("View")
		);
	},

	hostel(frm) {
		frm.set_value("block", "");
		frm.set_value("floor", "");
	},

	block(frm) {
		frm.set_value("floor", "");
	},

	room_type(frm) {
		const beds = H360_ROOM_TYPE_BEDS[frm.doc.room_type];
		if (beds) frm.set_value("capacity", beds);
	},

	render_beds(frm) {
		const wrapper = frm.get_field("beds_html").$wrapper;
		if (frm.is_new()) {
			wrapper.html(
				`<p class="text-muted small">${__(
					"Beds are created when the room is saved, one for each unit of capacity."
				)}</p>`
			);
			return;
		}
		frm.call({ doc: frm.doc, method: "get_beds" }).then((r) => {
			const colours = {
				Vacant: "green",
				Occupied: "blue",
				Reserved: "orange",
				Maintenance: "gray",
			};
			const chips = (r.message || []).map((bed) => {
				const resident = bed.current_student
					? `<div class="small">${frappe.utils.escape_html(bed.current_student_name || bed.current_student)}</div>`
					: "";
				return `<a class="h360-bed-chip" href="${frappe.utils.get_form_link("Hostel Bed", bed.name)}">
						<span class="indicator-pill ${colours[bed.status] || "gray"}">
							${__("Bed {0}", [frappe.utils.escape_html(bed.bed_label)])} · ${__(bed.status)}
						</span>${resident}
					</a>`;
			});
			wrapper.html(
				`<div class="d-flex flex-wrap" style="gap: var(--padding-md)">${chips.join("")}</div>`
			);
		});
	},
});
