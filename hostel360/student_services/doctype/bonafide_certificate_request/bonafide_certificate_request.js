// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Bonafide Certificate Request", {
	setup(frm) {
		frm.set_query("student", () => ({
			filters: { status: "Active", validation_status: "Validated" },
		}));
	},

	refresh(frm) {
		if (frm.is_new()) return;
		const doc = frm.doc;
		const is_approver = frappe.user.has_role([
			"Hostel Warden",
			"Deputy Warden",
			"Hostel Administrator",
			"System Manager",
		]);

		if (doc.status === "Pending" && is_approver) {
			frm.add_custom_button(__("Approve"), () =>
				frappe.confirm(
					__("Approve and number the certificate for {0}?", [frappe.utils.escape_html(doc.student_name)]),
					() => frm.call({ method: "approve", doc, freeze: true }).then(() => frm.reload_doc())
				)
			).addClass("btn-primary");
			frm.add_custom_button(__("Reject"), () =>
				frappe.prompt(
					[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason for rejection"), reqd: 1 }],
					(values) =>
						frm.call({ method: "reject", doc, args: values, freeze: true }).then(() => frm.reload_doc()),
					__("Reject Request"),
					__("Reject")
				)
			);
		}

		if (["Approved", "Issued"].includes(doc.status)) {
			const download = frm.add_custom_button(__("Download Certificate"), () => {
				const params = new URLSearchParams({
					doctype: doc.doctype,
					name: doc.name,
					format: "Bonafide Certificate",
					no_letterhead: 1,
				});
				window.open(`/api/method/frappe.utils.print_format.download_pdf?${params.toString()}`);
			});
			// Once handed over, downloading again is the main thing left to do.
			if (doc.status === "Issued") download.addClass("btn-primary");

			const url = frm.doc.__onload && frm.doc.__onload.verification_url;
			if (url) {
				frm.add_custom_button(__("Copy Verification Link"), () => frappe.utils.copy_to_clipboard(url), __("More"));
			}
		}

		if (doc.status === "Approved" && frm.perm[0]?.write) {
			frm.add_custom_button(__("Mark Handed Over"), () =>
				frm.call({ method: "mark_issued", doc, freeze: true }).then(() => frm.reload_doc())
			).addClass("btn-primary");
		}

		if (["Approved", "Issued"].includes(doc.status) && doc.valid_till < frappe.datetime.get_today()) {
			frm.dashboard.set_headline_alert(
				__("Validity ended on {0}. Verification now reports it as expired.", [
					frappe.datetime.str_to_user(doc.valid_till),
				]),
				"orange"
			);
		}
	},
});
