import frappe

from hostel360.setup.roles import GATE_ROLES, OPERATIONS_ROLE
from hostel360.utils.context import has_any_role


def has_app_permission() -> bool:
	"""Show Hostel360 on the apps screen for hostel staff, gate security and HyFib operations."""
	return has_any_role(GATE_ROLES | {OPERATIONS_ROLE})
