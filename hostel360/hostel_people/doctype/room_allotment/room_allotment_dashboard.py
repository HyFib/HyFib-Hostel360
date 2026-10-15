from frappe import _


def get_data():
	return {
		"fieldname": "previous_allotment",
		"transactions": [{"label": _("Transfers"), "items": ["Room Allotment"]}],
	}
