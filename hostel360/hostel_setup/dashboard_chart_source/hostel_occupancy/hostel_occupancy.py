# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _

# Stacking order on the chart. Occupied sits at the bottom of each bar because it is the figure
# wardens read first; Reserved next to it, since a reserved bed is about to be occupied.
BED_STATES = ("Occupied", "Reserved", "Vacant", "Maintenance")


@frappe.whitelist()
def get(
	chart_name: str | None = None,
	chart: str | dict | None = None,
	no_cache: bool | int | None = None,
	filters: str | dict | list | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	timespan: str | None = None,
	time_interval: str | None = None,
	heatmap_year: str | int | None = None,
	refresh: bool | int | None = None,
) -> dict:
	"""Active beds per hostel, split by bed status, for the "Occupancy by Hostel" chart.

	Counted through `frappe.get_list`, so a deputy warden sees only the hostels their user
	permissions allow. The date arguments are part of the chart source signature and do not apply
	to a point-in-time count.
	"""
	if not frappe.has_permission("Hostel Bed", "read"):
		frappe.throw(_("You are not permitted to view hostel occupancy."), frappe.PermissionError)

	rows = frappe.get_list(
		"Hostel Bed",
		filters={"is_active": 1},
		fields=["hostel", "status", {"COUNT": "*", "as": "beds"}],
		group_by="hostel, status",
		order_by="hostel asc",
		limit_page_length=0,
	)

	counts: dict[str, dict[str, int]] = {}
	for row in rows:
		if not row.hostel:
			continue
		counts.setdefault(row.hostel, {})[row.status] = int(row.beds or 0)

	if not counts:
		return {"labels": [], "datasets": []}

	# Hostels are named by their short code (BH1, GH2), which is also what fits under a bar in a
	# half-width dashboard chart; full names would be cut to an ellipsis.
	hostels = sorted(counts)
	return {
		"labels": hostels,
		"datasets": [
			{"name": _(state), "values": [counts[hostel].get(state, 0) for hostel in hostels]}
			for state in BED_STATES
		],
	}
