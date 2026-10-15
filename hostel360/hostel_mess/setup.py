"""Default records for the Hostel Mess module. Safe to run on every migrate."""

import frappe

from hostel360.hostel_mess.utils import DEFAULT_SETTINGS, SETTINGS_DOCTYPE

# item name, local (Tamil) name, item code, category, uom, reorder level, perishable
STARTER_ITEMS = (
	("Ponni Rice (Boiled)", "புழுங்கல் அரிசி", "GRN-001", "Grains", "Kg", 500, 0),
	("Raw Rice", "பச்சரிசி", "GRN-002", "Grains", "Kg", 150, 0),
	("Idli Rice", "இட்லி அரிசி", "GRN-003", "Grains", "Kg", 200, 0),
	("Wheat Flour (Atta)", "கோதுமை மாவு", "GRN-004", "Grains", "Kg", 50, 0),
	("Rava (Semolina)", "ரவை", "GRN-005", "Grains", "Kg", 25, 0),
	("Vermicelli (Semiya)", "சேமியா", "GRN-006", "Grains", "Kg", 10, 0),
	("Toor Dal", "துவரம் பருப்பு", "PUL-001", "Pulses", "Kg", 50, 0),
	("Urad Dal", "உளுத்தம் பருப்பு", "PUL-002", "Pulses", "Kg", 50, 0),
	("Moong Dal", "பாசிப் பருப்பு", "PUL-003", "Pulses", "Kg", 20, 0),
	("Chana Dal", "கடலைப் பருப்பு", "PUL-004", "Pulses", "Kg", 20, 0),
	("Bengal Gram (Kondakadalai)", "கொண்டைக்கடலை", "PUL-005", "Pulses", "Kg", 10, 0),
	("Sunflower Oil", "சூரியகாந்தி எண்ணெய்", "OIL-001", "Oil and Ghee", "Litre", 50, 0),
	("Gingelly Oil", "நல்லெண்ணெய்", "OIL-002", "Oil and Ghee", "Litre", 15, 0),
	("Groundnut Oil", "கடலை எண்ணெய்", "OIL-003", "Oil and Ghee", "Litre", 15, 0),
	("Ghee", "நெய்", "OIL-004", "Oil and Ghee", "Kg", 2, 0),
	("Sugar", "சர்க்கரை", "OTH-001", "Other", "Kg", 25, 0),
	("Salt", "உப்பு", "OTH-002", "Other", "Kg", 10, 0),
	("Tea Powder", "தேயிலைத் தூள்", "OTH-003", "Other", "Kg", 5, 0),
	("Coffee Powder", "காபித் தூள்", "OTH-004", "Other", "Kg", 5, 0),
	("Milk", "பால்", "DRY-001", "Dairy", "Litre", 0, 1),
	("Curd", "தயிர்", "DRY-002", "Dairy", "Litre", 0, 1),
	("Onion", "பெரிய வெங்காயம்", "VEG-001", "Vegetables", "Kg", 50, 1),
	("Small Onion (Shallots)", "சின்ன வெங்காயம்", "VEG-002", "Vegetables", "Kg", 10, 1),
	("Tomato", "தக்காளி", "VEG-003", "Vegetables", "Kg", 30, 1),
	("Potato", "உருளைக்கிழங்கு", "VEG-004", "Vegetables", "Kg", 30, 1),
	("Coconut", "தேங்காய்", "VEG-005", "Vegetables", "Nos", 50, 1),
	("Curry Leaves", "கறிவேப்பிலை", "VEG-006", "Vegetables", "Kg", 0, 1),
	("Tamarind", "புளி", "SPC-001", "Spices", "Kg", 10, 0),
	("Chilli Powder", "மிளகாய்த் தூள்", "SPC-002", "Spices", "Kg", 5, 0),
	("Turmeric Powder", "மஞ்சள் தூள்", "SPC-003", "Spices", "Kg", 2, 0),
	("Coriander Powder", "மல்லித் தூள்", "SPC-004", "Spices", "Kg", 5, 0),
	("Mustard Seeds", "கடுகு", "SPC-005", "Spices", "Kg", 2, 0),
	("Cumin Seeds", "சீரகம்", "SPC-006", "Spices", "Kg", 2, 0),
	("Black Pepper", "மிளகு", "SPC-007", "Spices", "Kg", 1, 0),
	("Dry Red Chilli", "காய்ந்த மிளகாய்", "SPC-008", "Spices", "Kg", 5, 0),
	("Eggs", "முட்டை", "MEG-001", "Meat and Eggs", "Nos", 300, 1),
	("Chicken", "கோழிக்கறி", "MEG-002", "Meat and Eggs", "Kg", 0, 1),
	("Banana", "வாழைப்பழம்", "FRT-001", "Fruits", "Nos", 0, 1),
	("LPG Cylinder (Commercial 19 kg)", "சமையல் எரிவாயு உருளை", "FUL-001", "Fuel", "Cylinder", 2, 0),
	("Dishwash Liquid", "பாத்திரம் கழுவும் திரவம்", "CLN-001", "Cleaning", "Litre", 5, 0),
)


def install_defaults():
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return
	set_settings_defaults()
	create_starter_items()


def set_settings_defaults() -> None:
	"""Write defaults for settings never saved, leaving administrators' choices alone."""
	stored = set(
		frappe.db.sql_list(
			"select field from `tabSingles` where doctype = %(doctype)s", {"doctype": SETTINGS_DOCTYPE}
		)
	)
	for fieldname, value in DEFAULT_SETTINGS.items():
		if fieldname not in stored:
			frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, value)


def create_starter_items() -> None:
	"""A starter list of common South Indian mess provisions, only on a fresh install."""
	if frappe.db.count("Mess Item"):
		return
	for item_name, local_name, item_code, category, uom, reorder_level, perishable in STARTER_ITEMS:
		frappe.get_doc(
			{
				"doctype": "Mess Item",
				"item_name": item_name,
				"local_name": local_name,
				"item_code": item_code,
				"category": category,
				"uom": uom,
				"reorder_level": reorder_level,
				"is_perishable": perishable,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
