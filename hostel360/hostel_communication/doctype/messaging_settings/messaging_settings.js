// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Messaging Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Message Templates"), () => frappe.set_route("List", "Message Template"), __("View"));
		frm.add_custom_button(__("Message Log"), () => frappe.set_route("List", "Message Log"), __("View"));

		frm.add_custom_button(__("Send Test Message"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Please save the settings before sending a test message."));
				return;
			}
			const dialog = new frappe.ui.Dialog({
				title: __("Send Test Message"),
				fields: [
					{
						fieldname: "channel",
						fieldtype: "Select",
						label: __("Channel"),
						options: ["SMS", "WhatsApp", "Email"],
						default: "SMS",
						reqd: 1,
					},
					{
						fieldname: "to",
						fieldtype: "Data",
						label: __("Send To"),
						reqd: 1,
						description: __("A 10-digit mobile number for SMS or WhatsApp, or an e-mail address."),
					},
				],
				primary_action_label: __("Send"),
				primary_action(values) {
					frm.call({
						method: "send_test_message",
						doc: frm.doc,
						args: values,
						freeze: true,
						freeze_message: __("Sending..."),
					}).then((r) => {
						if (!r.message) return;
						dialog.hide();
						frappe.show_alert({
							message: r.message.message,
							indicator: r.message.ok ? "green" : "red",
						}, 7);
					});
				},
			});
			dialog.show();
		});

		if (frm.doc.test_mode) {
			frm.dashboard.set_headline_alert(
				__("Test mode is on: messages are written to the Message Log but not sent."),
				"orange"
			);
		}
	},

	sms_provider(frm) {
		if (frm.doc.sms_provider === "MSG91" && !frm.doc.sms_api_url) {
			frm.set_df_property("sms_api_url", "placeholder", "https://control.msg91.com/api/v5/flow");
		}
	},
});
