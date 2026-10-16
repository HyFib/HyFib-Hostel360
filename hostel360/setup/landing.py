"""Where each staff member's desk opens, and which Hostel360 areas their dock offers.

Every desk role has a home: the administration overview for the people who run the hostel,
the mess for mess staff, the gate for security staff and the operations console for HyFib.
Frappe opens an app at one fixed route, so the app's route is ``/staff`` (``www/staff.py``),
which sends each person on to their own home.
"""

import frappe
from frappe.utils import has_common
from frappe.utils.caching import request_cache

# Workspaces in order of preference: a person starts on the first one their roles may open.
LANDING_WORKSPACES = (
	"Hostel Admin",
	"Mess",
	"Gate",
	"HyFib Operations",
	"Student Services",
	"People and Rooms",
	"Fees",
)
APP = "hostel360"


def workspace_route(name: str) -> str:
	"""The desk route of a public workspace (the desk slugs the title the same way)."""
	return "/desk/" + name.lower().replace(" ", "-")


@request_cache
def workspace_roles() -> dict[str, set[str]]:
	"""Hostel360 workspace name -> the roles it is shown to (an empty set means everyone)."""
	modules = frappe.get_all("Module Def", filters={"app_name": APP}, pluck="name")
	workspaces = frappe.get_all("Workspace", filters={"module": ("in", modules), "public": 1}, pluck="name")
	roles = {name: set() for name in workspaces}
	for row in frappe.get_all(
		"Has Role",
		filters={"parenttype": "Workspace", "parent": ("in", workspaces or [""])},
		fields=["parent", "role"],
	):
		roles[row.parent].add(row.role)
	return roles


def permitted_workspaces(user: str | None = None) -> set[str]:
	"""The Hostel360 workspaces ``user`` may open."""
	user_roles = frappe.get_roles(user or frappe.session.user)
	if "System Manager" in user_roles:
		return set(workspace_roles())
	return {name for name, roles in workspace_roles().items() if not roles or has_common(user_roles, roles)}


def landing_route(user: str | None = None) -> str:
	"""The desk route ``user`` starts on: their first permitted workspace, else the desktop."""
	permitted = permitted_workspaces(user)
	return next((workspace_route(name) for name in LANDING_WORKSPACES if name in permitted), "/desk")


def filter_dock(bootinfo) -> None:
	"""Keep only the areas a person works in on the Hostel360 dock.

	Frappe shows a dock entry to anyone who can open one item of its sidebar, so a mess manager,
	who may read fee invoices and student records for mess billing, would also get the Fees and
	People and Rooms areas, and HyFib operations staff the Hostel Admin area through its settings
	links. An entry whose sidebar belongs to a workspace is kept only when the person may open
	that workspace; entries without one (Messaging, Data Migration) keep Frappe's rule, and an
	entry whose sidebar the person cannot open at all is dropped here as the desk would drop it.
	Records stay reachable through links and search as their permissions allow.
	"""
	rail = (bootinfo.get("dock") or {}).get(APP)
	if not rail:
		return
	workspaces = workspace_roles()
	permitted = permitted_workspaces()
	shells = bootinfo.get("module_sidebars")

	def keep(row) -> bool:
		if row.get("link_type") != "Sidebar":
			return True
		shell = row.get("link_to")
		if shells is not None and shell not in shells:
			return False
		return shell not in workspaces or shell in permitted

	bootinfo.dock[APP] = [row for row in rail if keep(row)]
