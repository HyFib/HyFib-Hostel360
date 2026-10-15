"""Hostel People has no seed data of its own (masters are installed by Hostel Setup).

On every migrate it re-derives room and hostel occupancy counters from the beds, which
heals counters after manual database corrections or an interrupted data migration.
"""

import frappe


def install_defaults() -> None:
	from hostel360.hostel_setup.occupancy import refresh_all

	if not frappe.db.table_exists("Hostel Bed") or not frappe.db.table_exists("Hostel Room"):
		return
	refresh_all()
