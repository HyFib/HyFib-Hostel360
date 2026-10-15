// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const MESS_REDUCTION_METHOD = "hostel360.hostel_mess.doctype.mess_reduction_application.mess_reduction_application";

frappe.ui.form.on("Mess Reduction Application", {
	setup(frm) {
		frm.set_query("student", () => ({ filters: { status: "Active", mess_enrolled: 1 } }));
	},

	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.status === "Pending" && frm.perm[0]?.write) {
			frm.add_custom_button(__("Approve"), () => frm.events.decide(frm, true), __("Decision"));
			frm.add_custom_button(__("Reject"), () => frm.events.decide(frm, false), __("Decision"));
			frm.page.set_inner_btn_group_as_primary(__("Decision"));
		}
		if (["Pending", "Approved"].includes(frm.doc.status) && frm.perm[0]?.write) {
			frm.add_custom_button(__("Cancel Application"), () => frm.events.cancel_application(frm));
		}
		if (frm.doc.status === "Pending") {
			frm.set_intro(__("Awaiting approval."), "orange");
		} else if (frm.doc.status === "Rejected" && frm.doc.approval_remarks) {
			frm.set_intro(__("Rejected: {0}", [frappe.utils.escape_html(frm.doc.approval_remarks)]), "red");
		}
	},

	from_date(frm) {
		frm.events.set_days(frm);
	},

	to_date(frm) {
		frm.events.set_days(frm);
	},

	set_days(frm) {
		if (frm.doc.from_date && frm.doc.to_date) {
			const days = frappe.datetime.get_day_diff(frm.doc.to_date, frm.doc.from_date) + 1;
			frm.set_value("number_of_days", days > 0 ? days : 0);
		}
	},

	decide(frm, approve) {
		frappe.prompt(
			[
				{
					fieldname: "remarks",
					fieldtype: "Small Text",
					label: approve ? __("Remarks") : __("Reason for Rejection"),
					reqd: approve ? 0 : 1,
				},
			],
			(values) => {
				frappe
					.call({
						method: `${MESS_REDUCTION_METHOD}.decide`,
						args: { name: frm.doc.name, approve: approve ? 1 : 0, remarks: values.remarks },
						freeze: true,
					})
					.then(() => frm.reload_doc());
			},
			approve ? __("Approve Mess Reduction") : __("Reject Mess Reduction"),
			approve ? __("Approve") : __("Reject")
		);
	},

	cancel_application(frm) {
		frappe.confirm(__("Cancel this mess reduction application?"), () => {
			frappe
				.call({
					method: `${MESS_REDUCTION_METHOD}.cancel_application`,
					args: { name: frm.doc.name },
					freeze: true,
				})
				.then(() => frm.reload_doc());
		});
	},
});
