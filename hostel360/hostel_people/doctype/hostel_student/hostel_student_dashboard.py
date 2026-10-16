import frappe
from frappe import _

# Records from other modules appear here once those modules are installed. Feedback is
# left out on purpose: anonymous feedback must not be traceable from the student form.
GROUPS = (
	("Hostel", ("Room Allotment", "Home Visit Leave", "Bonafide Certificate Request")),
	("Fees", ("Hostel Fee Invoice", "Hostel Payment")),
	("Mess", ("Mess Reduction Application", "Mess Bill")),
	("Support", ("Hostel Grievance",)),
)


def get_data():
	transactions = []
	for label, doctypes in GROUPS:
		items = [doctype for doctype in doctypes if frappe.db.exists("DocType", doctype)]
		if items:
			transactions.append({"label": _(label), "items": items})
	return {"fieldname": "student", "transactions": transactions}
