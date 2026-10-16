from frappe import _


def get_data():
	return {
		"fieldname": "hostel",
		"transactions": [
			{"label": _("Structure"), "items": ["Hostel Block", "Hostel Floor", "Hostel Room", "Hostel Bed"]},
			{"label": _("Residents"), "items": ["Room Allotment"]},
		],
	}
