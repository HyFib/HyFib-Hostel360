"""Roles shipped with Hostel360.

Every desk role requires two-factor authentication (tender T&C 28). Portal roles
(students and parents) sign in with a one-time password to their registered mobile.
"""

ADMIN_ROLE = "Hostel Administrator"
WARDEN_ROLE = "Hostel Warden"
DEPUTY_WARDEN_ROLE = "Deputy Warden"
OFFICE_ROLE = "Hostel Office Staff"
MESS_MANAGER_ROLE = "Mess Manager"
MESS_STAFF_ROLE = "Mess Staff"
OPERATIONS_ROLE = "HyFib Operations"
AUDITOR_ROLE = "Institution Auditor"
STUDENT_ROLE = "Hostel Student"
PARENT_ROLE = "Hostel Parent"

ADMIN_ROLES = frozenset({ADMIN_ROLE, "System Manager"})
APPROVER_ROLES = frozenset({ADMIN_ROLE, WARDEN_ROLE, DEPUTY_WARDEN_ROLE})
MESS_ROLES = frozenset({MESS_MANAGER_ROLE, MESS_STAFF_ROLE})
STAFF_ROLES = frozenset(
	{
		ADMIN_ROLE,
		WARDEN_ROLE,
		DEPUTY_WARDEN_ROLE,
		OFFICE_ROLE,
		MESS_MANAGER_ROLE,
		MESS_STAFF_ROLE,
		AUDITOR_ROLE,
		"System Manager",
	}
)
PORTAL_ROLES = frozenset({STUDENT_ROLE, PARENT_ROLE})

# name, desk_access, two_factor_auth, description
ROLES = (
	(ADMIN_ROLE, 1, 1, "Full control of hostels, people, fees, mess and settings."),
	(WARDEN_ROLE, 1, 1, "Chief warden: approvals across all hostels and escalated grievances."),
	(DEPUTY_WARDEN_ROLE, 1, 1, "Approvals and day-to-day operations for assigned hostels and blocks."),
	(OFFICE_ROLE, 1, 1, "Registrations, room allotment, fee collection and receipts."),
	(MESS_MANAGER_ROLE, 1, 1, "Mess stock, menu, indents, reductions and monthly mess billing."),
	(MESS_STAFF_ROLE, 1, 1, "Records daily stock receipts and issues for the mess."),
	(OPERATIONS_ROLE, 1, 1, "HyFib Solutions support and operations: alerts, health and tickets."),
	(AUDITOR_ROLE, 1, 1, "Read-only access to dashboards, reports and monitoring for institution officials."),
	(STUDENT_ROLE, 0, 0, "Hostel resident. Uses the student portal with OTP login."),
	(PARENT_ROLE, 0, 0, "Parent or guardian of a resident. Uses the parent portal with OTP login."),
)

# Role granted to the desk user created for each Hostel Staff designation.
DESIGNATION_ROLES = {
	"Warden": [WARDEN_ROLE],
	"Deputy Warden": [DEPUTY_WARDEN_ROLE],
	"Office Staff": [OFFICE_ROLE],
	"Mess Manager": [MESS_MANAGER_ROLE],
	"Mess Staff": [MESS_STAFF_ROLE],
	"Hostel Administrator": [ADMIN_ROLE],
}

# Designations whose desk access is restricted to their assigned hostels through
# User Permissions on the Hostel DocType.
HOSTEL_SCOPED_DESIGNATIONS = frozenset({"Deputy Warden", "Mess Staff", "Office Staff"})
