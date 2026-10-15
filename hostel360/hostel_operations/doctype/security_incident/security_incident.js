// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

const SECURITY_INCIDENT_COLOURS = {
	Open: "red",
	Reported: "blue",
	Closed: "green",
	"Not Reportable": "gray",
};

frappe.ui.form.on("Security Incident", {
	onload(frm) {
		if (frm.is_new() && !frm.doc.detected_on) {
			frm.set_value("detected_on", frappe.datetime.now_datetime());
		}
	},

	refresh(frm) {
		render_reporting_procedure(frm);
		show_deadline(frm);
		if (frm.is_new() || !frappe.user.has_role(["HyFib Operations", "System Manager"])) {
			return;
		}
		if (frm.doc.cert_in_reportable && !frm.doc.reported_to_certin_on) {
			frm.add_custom_button(
				__("Reported to CERT-In Now"),
				() => record_report(frm, "reported_to_certin_on"),
				__("Record")
			);
		}
		if (frm.doc.cert_in_reportable && !frm.doc.reported_to_institution_on) {
			frm.add_custom_button(
				__("Institution Informed Now"),
				() => record_report(frm, "reported_to_institution_on"),
				__("Record")
			);
		}
		if (frm.doc.ops_alert) {
			frm.add_custom_button(__("Open Alert"), () => frappe.set_route("Form", "Ops Alert", frm.doc.ops_alert));
		}
	},

	detected_on(frm) {
		show_deadline(frm);
	},
});

function record_report(frm, fieldname) {
	frm.set_value(fieldname, frappe.datetime.now_datetime());
	frm.save();
}

function show_deadline(frm) {
	const doc = frm.doc;
	frm.page.set_indicator(__(doc.status || "Open"), SECURITY_INCIDENT_COLOURS[doc.status] || "red");
	if (!doc.detected_on || !doc.cert_in_reportable || doc.status !== "Open") {
		frm.dashboard.clear_headline();
		return;
	}
	const deadline = moment(frappe.datetime.str_to_obj(doc.detected_on)).add(6, "hours");
	const minutes = deadline.diff(moment(), "minutes");
	const pending = [];
	if (!doc.reported_to_certin_on) {
		pending.push(__("CERT-In"));
	}
	if (!doc.reported_to_institution_on) {
		pending.push(__("the institution"));
	}
	const due = frappe.datetime.str_to_user(deadline.format("YYYY-MM-DD HH:mm:ss"));
	if (minutes < 0) {
		frm.dashboard.set_headline(
			__("The six-hour reporting deadline passed at {0}. Still to inform: {1}.", [due, pending.join(", ")]),
			"red"
		);
	} else {
		const hours = Math.floor(minutes / 60);
		frm.dashboard.set_headline(
			__("Report to {0} by {1} ({2} h {3} min left).", [pending.join(", "), due, hours, minutes % 60]),
			minutes <= 120 ? "orange" : "blue"
		);
	}
}

function render_reporting_procedure(frm) {
	const field = frm.get_field("reporting_procedure");
	if (!field) {
		return;
	}
	const steps = [
		__("Within six hours of detection, e-mail the incident report to {0} (or use the incident reporting form on {1}). Give the time of detection, the CERT-In category, the systems affected and what has been done so far.", [
			"<b>incident@cert-in.org.in</b>",
			"<a href='https://www.cert-in.org.in' target='_blank' rel='noopener noreferrer'>www.cert-in.org.in</a>",
		]),
		__("Inform the institution within the same six hours: the Hostel Administrator or the Principal of ACGCET, by phone and in writing."),
		__("If students' or parents' personal data may be affected, tick Personal Data Involved: the Data Protection Board and the affected persons must also be informed."),
		__("Preserve evidence before cleaning up: maldet and ClamAV reports, quarantined files, WAF and application logs (kept for 180 days)."),
		__("Record the time of each report here, with the CERT-In acknowledgement number. Closing the incident needs both reports and an assessment note."),
	];
	field.$wrapper.html(
		`<ol class="text-muted small" style="padding-left: 18px; margin-bottom: 0">${steps
			.map((step) => `<li style="margin-bottom: 6px">${step}</li>`)
			.join("")}</ol>`
	);
}
