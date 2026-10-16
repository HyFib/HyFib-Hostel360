// Copyright (c) 2026, HyFib Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Hostel Occupancy"] = {
	method: "hostel360.hostel_setup.dashboard_chart_source.hostel_occupancy.hostel_occupancy.get",
	filters: [],
};
