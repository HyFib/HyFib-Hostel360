// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Ops Metric Sample", {
	refresh(frm) {
		frm.set_intro(__("Samples are written by health checks and the host agent. Edit them only to correct bad data."));
	},
});
