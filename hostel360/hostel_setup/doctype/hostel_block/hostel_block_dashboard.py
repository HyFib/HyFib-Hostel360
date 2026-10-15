from frappe import _


def get_data():
	return {
		"fieldname": "block",
		"transactions": [{"label": _("Structure"), "items": ["Hostel Floor", "Hostel Room", "Hostel Bed"]}],
	}
