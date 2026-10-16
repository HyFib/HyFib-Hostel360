// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.provide("hostel360.allotment");

// Hostels a student of this gender may live in.
hostel360.allotment.hostel_genders = (gender) => {
	if (gender === "Male") return ["Male", "Mixed"];
	if (gender === "Female") return ["Female", "Mixed"];
	return ["Mixed"];
};

// Also finds a returning student who last lived in another hostel, by the exact register
// or mobile number.
hostel360.allotment.student_query = "hostel360.hostel_people.queries.allottable_student_query";

hostel360.allotment.vacant_bed_query = (filters) => ({
	query: "hostel360.hostel_people.page.room_board.room_board.search_vacant_beds",
	filters,
});

hostel360.allotment.transfer_dialog = (allotment, on_done) => {
	const dialog = new frappe.ui.Dialog({
		title: __("Transfer {0}", [allotment.student_name]),
		fields: [
			{
				fieldname: "hostel",
				fieldtype: "Link",
				label: __("Hostel"),
				options: "Hostel",
				default: allotment.hostel,
				get_query: () => ({
					filters: {
						is_active: 1,
						gender: ["in", hostel360.allotment.hostel_genders(allotment.gender)],
					},
				}),
			},
			{
				fieldname: "new_bed",
				fieldtype: "Link",
				label: __("New Bed"),
				options: "Hostel Bed",
				reqd: 1,
				get_query: () =>
					hostel360.allotment.vacant_bed_query({
						hostel: dialog.get_value("hostel"),
						student: allotment.student,
						exclude: allotment.bed,
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
		primary_action(values) {
			frappe
				.call({
					method: "run_doc_method",
					args: {
						dt: "Room Allotment",
						dn: allotment.name,
						method: "transfer",
						args: {
							new_bed: values.new_bed,
							from_date: values.from_date,
							remarks: values.remarks,
						},
					},
					freeze: true,
					freeze_message: __("Transferring..."),
				})
				.then((r) => {
					dialog.hide();
					frappe.show_alert({
						message: __("Transferred. New allotment {0}.", [r.message]),
						indicator: "green",
					});
					on_done && on_done(r.message);
				});
		},
	});
	dialog.show();
	return dialog;
};

hostel360.allotment.vacate_dialog = (allotment, on_done) => {
	const dialog = new frappe.ui.Dialog({
		title: __("Vacate {0}", [allotment.student_name]),
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
		primary_action(values) {
			frappe
				.call({
					method: "run_doc_method",
					args: {
						dt: "Room Allotment",
						dn: allotment.name,
						method: "vacate",
						args: values,
					},
					freeze: true,
					freeze_message: __("Vacating..."),
				})
				.then(() => {
					dialog.hide();
					frappe.show_alert({ message: __("Bed released"), indicator: "green" });
					on_done && on_done();
				});
		},
	});
	dialog.show();
	return dialog;
};

frappe.ui.form.on("Room Allotment", {
	setup(frm) {
		frm.set_query("student", () => {
			const filters = { status: ["in", ["Active", "Applied"]] };
			if (frm.doc.allotment_type === "New") filters.current_allotment = ["is", "not set"];
			else filters.current_allotment = ["is", "set"];
			return { query: hostel360.allotment.student_query, filters };
		});
		frm.set_query("hostel", () => {
			const filters = { is_active: 1 };
			if (frm.doc.gender) {
				filters.gender = ["in", hostel360.allotment.hostel_genders(frm.doc.gender)];
			}
			return { filters };
		});
		frm.set_query("room", () => {
			const filters = {
				is_active: 1,
				status: ["not in", ["Full", "Under Maintenance", "Blocked"]],
			};
			if (frm.doc.hostel) filters.hostel = frm.doc.hostel;
			return { filters };
		});
		frm.set_query("bed", () =>
			hostel360.allotment.vacant_bed_query({
				hostel: frm.doc.hostel,
				room: frm.doc.room,
				student: frm.doc.student,
			})
		);
		frm.set_query("previous_allotment", () => ({
			filters: { student: frm.doc.student, docstatus: 1, status: "Active" },
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status === "Active" && frm.perm[0]?.submit) {
			frm.add_custom_button(__("Transfer"), () =>
				hostel360.allotment.transfer_dialog(frm.doc, (name) =>
					frappe.set_route("Form", "Room Allotment", name)
				)
			);
			frm.add_custom_button(__("Vacate"), () =>
				hostel360.allotment.vacate_dialog(frm.doc, () => frm.reload_doc())
			);
		}
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Room Board"),
				() => frappe.set_route("room-board", { hostel: frm.doc.hostel }),
				__("View")
			);
			frm.add_custom_button(
				__("Student History"),
				() =>
					frappe.set_route("query-report", "Room Allotment History", {
						student: frm.doc.student,
					}),
				__("View")
			);
		}
		if (frm.doc.docstatus === 1 && frm.doc.status === "Vacated") {
			frm.dashboard.set_headline_alert(
				__("Vacated on {0}: {1}", [
					frappe.datetime.str_to_user(frm.doc.vacated_on),
					frappe.utils.escape_html(frm.doc.vacate_reason || ""),
				]),
				"gray"
			);
		}
	},

	student(frm) {
		if (!frm.doc.student) return;
		if (frm.doc.allotment_type === "Transfer") {
			frappe.db
				.get_value("Hostel Student", frm.doc.student, "current_allotment")
				.then((r) => frm.set_value("previous_allotment", r.message?.current_allotment || ""));
		}
	},

	allotment_type(frm) {
		if (frm.doc.allotment_type === "New") frm.set_value("previous_allotment", "");
		else frm.trigger("student");
	},

	hostel(frm) {
		if (frm.doc.room && !frm.doc.room.startsWith(`${frm.doc.hostel}-`)) {
			frm.set_value("room", "");
			frm.set_value("bed", "");
		}
	},

	room(frm) {
		if (frm.doc.bed && frm.doc.room && !frm.doc.bed.startsWith(`${frm.doc.room}-`)) {
			frm.set_value("bed", "");
		}
	},

	bed(frm) {
		if (!frm.doc.bed) return;
		frappe.db.get_value("Hostel Bed", frm.doc.bed, ["hostel", "room", "block"]).then((r) => {
			const bed = r.message || {};
			if (bed.hostel && frm.doc.hostel !== bed.hostel) frm.set_value("hostel", bed.hostel);
			if (bed.room && frm.doc.room !== bed.room) frm.set_value("room", bed.room);
			frm.set_value("block", bed.block || "");
		});
	},
});
