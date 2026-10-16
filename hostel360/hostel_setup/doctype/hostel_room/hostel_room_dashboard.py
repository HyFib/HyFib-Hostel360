from frappe import _


def get_data():
	return {
		"fieldname": "room",
		"transactions": [
			{"label": _("Beds"), "items": ["Hostel Bed"]},
			{"label": _("Residents"), "items": ["Room Allotment"]},
		],
	}
