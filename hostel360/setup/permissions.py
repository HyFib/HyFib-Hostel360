import frappe

from hostel360.setup.roles import OPERATIONS_ROLE, STAFF_ROLES
from hostel360.utils.context import has_any_role


def has_app_permission() -> bool:
	"""Show Hostel360 on the apps screen for hostel staff and HyFib operations."""
	return has_any_role(STAFF_ROLES | {OPERATIONS_ROLE})
