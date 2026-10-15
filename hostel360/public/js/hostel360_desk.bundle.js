// Desk-wide helpers for HyFib Hostel360.
//
// Loaded on every desk page through `app_include_js`, so it stays small: formatting and status
// helpers that forms, list views and pages share. Feature code belongs with its DocType or page.

frappe.provide("hostel360");

(() => {
	// Indicator colours for the status values used across Hostel360. Pages and list views read
	// from here so that, say, "Overdue" is the same red on an invoice, a leave and the dashboard.
	// Colour names are Frappe indicator colours.
	const STATUS_COLOURS = {
		// beds and rooms
		Vacant: "green",
		Available: "green",
		Occupied: "blue",
		"Partially Occupied": "blue",
		Full: "orange",
		Reserved: "orange",
		Maintenance: "gray",
		"Under Maintenance": "gray",
		Blocked: "red",

		// people
		Applied: "gray",
		Active: "green",
		Suspended: "red",
		Vacated: "gray",
		Alumni: "gray",
		Validated: "green",
		Transferred: "blue",

		// approvals (leave, mess reduction, bonafide, indent)
		Pending: "orange",
		"Pending Approval": "orange",
		"Pending Parent Approval": "orange",
		"Pending Warden Approval": "orange",
		Approved: "green",
		Rejected: "red",
		Issued: "green",
		"Checked Out": "blue",
		Returned: "green",
		Ordered: "blue",
		Received: "green",

		// money and billing
		Unpaid: "orange",
		"Partly Paid": "yellow",
		Paid: "green",
		Overdue: "red",
		Unbilled: "orange",
		Billed: "green",
		Computed: "blue",
		Published: "green",
		Initiated: "gray",
		Success: "green",
		Failed: "red",
		Expired: "gray",
		Refunded: "purple",

		// grievances, tickets and alerts
		Open: "orange",
		"In Progress": "blue",
		Escalated: "red",
		Reopened: "orange",
		Resolved: "green",
		Closed: "gray",
		Acknowledged: "blue",
		"Waiting on Institution": "yellow",
		Suppressed: "gray",
		Info: "blue",
		Warning: "orange",
		Critical: "red",

		// jobs, imports and messages
		Draft: "gray",
		Queued: "gray",
		Running: "blue",
		Importing: "blue",
		Completed: "green",
		"Completed with Errors": "orange",
		Sent: "green",
		Skipped: "gray",
		Cancelled: "red",
	};

	Object.assign(hostel360, {
		BRAND_COLOUR: "#0B4F9C",
		ACCENT_COLOUR: "#19A7CE",
		STATUS_COLOURS,

		/** Indicator colour for a status value; unknown values fall back to gray. */
		status_colour(status) {
			return STATUS_COLOURS[status] || "gray";
		},

		/** `[label, colour, filter]` as `frappe.listview_settings.get_indicator` expects. */
		status_indicator(status, fieldname = "status") {
			if (!status) return;
			return [__(status), hostel360.status_colour(status), `${fieldname},=,${status}`];
		},

		/** Indicator pill HTML for pages and dialogs. */
		status_pill(status) {
			if (!status) return "";
			const colour = hostel360.status_colour(status);
			return `<span class="indicator-pill ${colour} h360-status-pill">${frappe.utils.escape_html(
				__(status)
			)}</span>`;
		},

		/**
		 * Rupees with Indian digit grouping, matching the server's `format_inr` used in print
		 * formats: 1234567.5 -> "₹12,34,567.50".
		 */
		format_inr(amount, precision = 2) {
			const value = flt(amount, precision);
			const sign = value < 0 ? "-" : "";
			const [whole, fraction] = Math.abs(value).toFixed(precision).split(".");
			let grouped = whole;
			if (whole.length > 3) {
				const head = whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
				grouped = `${head},${whole.slice(-3)}`;
			}
			return `${sign}₹${grouped}${precision ? "." + fraction : ""}`;
		},

		/** 9876543210 -> "98xxxxxx10", the same masking the server applies in logs. */
		mask_mobile(mobile) {
			const digits = String(mobile || "").replace(/\D/g, "");
			if (!digits) return "";
			if (digits.length < 4) return "x".repeat(digits.length);
			return digits.slice(0, 2) + "x".repeat(digits.length - 4) + digits.slice(-2);
		},
	});
})();
