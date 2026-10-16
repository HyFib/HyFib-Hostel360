// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const MESS_MEALS = ["Breakfast", "Lunch", "Snacks", "Dinner"];
const MESS_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

frappe.ui.form.on("Mess Menu", {
	setup(frm) {
		frm.set_query("mess", () => ({ filters: { is_active: 1 } }));
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.week_start_date) {
			// Menus are usually planned for the coming week.
			frm.set_value("week_start_date", mess_menu_monday(frappe.datetime.add_days(frappe.datetime.get_today(), 7)));
		}
	},

	refresh(frm) {
		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Copy Previous Week"), () => frm.events.copy_previous_week(frm));
			if (!(frm.doc.items || []).length) {
				frm.add_custom_button(__("Add Days and Meals"), () => frm.events.fill_week(frm));
			}
			if (!frm.is_new() && (frm.doc.items || []).length) {
				frm.add_custom_button(__("Publish"), () => frm.events.publish(frm)).addClass("btn-primary");
			}
		} else if (frm.doc.status === "Published") {
			frm.set_intro(
				__("Published on {0}. Residents see this menu on the student portal.", [
					frappe.datetime.str_to_user(frm.doc.published_on),
				]),
				"green"
			);
		}
	},

	week_start_date(frm) {
		if (!frm.doc.week_start_date) return;
		const monday = mess_menu_monday(frm.doc.week_start_date);
		if (monday !== frm.doc.week_start_date) {
			frappe.show_alert({
				message: __("Menus run Monday to Sunday; moved to Monday {0}.", [frappe.datetime.str_to_user(monday)]),
				indicator: "blue",
			});
			frm.set_value("week_start_date", monday);
			return;
		}
		frm.set_value("week_end_date", frappe.datetime.add_days(monday, 6));
		const items = frm.doc.items || [];
		if (!items.length) {
			frm.events.fill_week(frm);
		} else {
			// Keep the rows on the same weekdays when the week changes.
			items.forEach((row) => {
				const offset = Math.max(MESS_WEEKDAYS.indexOf(row.day), 0);
				row.menu_date = frappe.datetime.add_days(monday, offset);
			});
			frm.refresh_field("items");
		}
	},

	fill_week(frm) {
		if (!frm.doc.week_start_date) {
			frappe.msgprint(__("Select the week first."));
			return;
		}
		frm.clear_table("items");
		MESS_WEEKDAYS.forEach((day, offset) => {
			MESS_MEALS.forEach((meal) => {
				frm.add_child("items", {
					menu_date: frappe.datetime.add_days(frm.doc.week_start_date, offset),
					day,
					meal,
				});
			});
		});
		frm.refresh_field("items");
	},

	copy_previous_week(frm) {
		if (!frm.doc.mess || !frm.doc.week_start_date) {
			frappe.msgprint(__("Select the mess and the week first."));
			return;
		}
		const run = () =>
			frappe
				.call({
					method: "hostel360.hostel_mess.doctype.mess_menu.mess_menu.get_previous_week_items",
					args: { mess: frm.doc.mess, week_start_date: frm.doc.week_start_date },
					freeze: true,
				})
				.then((r) => {
					frm.clear_table("items");
					(r.message || []).forEach((row) => frm.add_child("items", row));
					frm.refresh_field("items");
					frm.dirty();
					frappe.show_alert({ message: __("Copied the previous menu. Review and save."), indicator: "green" });
				});
		const filled = (frm.doc.items || []).some((row) => row.dishes);
		if (filled) {
			frappe.confirm(__("Replace the meals entered here with the previous week's menu?"), run);
		} else {
			run();
		}
	},

	publish(frm) {
		const go = () =>
			frm.call({ doc: frm.doc, method: "publish", freeze: true }).then(() => {
				frm.reload_doc();
				frappe.show_alert({ message: __("Menu published"), indicator: "green" });
			});
		frappe.confirm(__("Publish this menu? Residents will be notified."), () => {
			if (frm.is_dirty()) {
				frm.save().then(go);
			} else {
				go();
			}
		});
	},
});

frappe.ui.form.on("Mess Menu Item", {
	menu_date(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.menu_date) return;
		const weekday = (frappe.datetime.str_to_obj(row.menu_date).getDay() + 6) % 7;
		frappe.model.set_value(cdt, cdn, "day", MESS_WEEKDAYS[weekday]);
	},
});

function mess_menu_monday(date) {
	const weekday = (frappe.datetime.str_to_obj(date).getDay() + 6) % 7;
	return frappe.datetime.add_days(date, -weekday);
}
