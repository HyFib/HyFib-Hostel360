# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

"""Checks the desk the app ships: workspaces, number cards, charts, sidebars, dock and onboarding.

These records are imported by `bench migrate` without running their controllers' validation, so a
label typo or a link to a renamed report would only show up as a blank block on someone's screen.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from hostel360.setup.roles import (
	AUDITOR_ROLE,
	MESS_MANAGER_ROLE,
	MESS_STAFF_ROLE,
	OFFICE_ROLE,
	OPERATIONS_ROLE,
)

WORKSPACES = ("Hostel Admin", "People and Rooms", "Fees", "Mess", "Student Services", "HyFib Operations")
SHELLS = (*WORKSPACES, "Messaging", "Data Migration")
MODULES = (
	"Hostel Setup",
	"Hostel People",
	"Hostel Fees",
	"Hostel Mess",
	"Student Services",
	"Hostel Communication",
	"Hostel Operations",
	"Hostel Migration",
)
ONBOARDING = "Get started with Hostel360"

# workspace content block type -> (child table, key the block uses to name its row's label)
BLOCK_TABLES = {
	"number_card": ("number_cards", "number_card_name"),
	"chart": ("charts", "chart_name"),
	"shortcut": ("shortcuts", "shortcut_name"),
}


def make_user(roles: list[str]) -> str:
	email = f"desk-{frappe.generate_hash(length=10)}@example.test"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"enabled": 1,
			"first_name": "Desk",
			"send_welcome_email": 0,
			"user_type": "System User",
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.no_welcome_mail = True
	# several users are created in one transaction; skip the sign-up throttle
	in_import, frappe.flags.in_import = frappe.flags.in_import, True
	try:
		user.insert(ignore_permissions=True)
	finally:
		frappe.flags.in_import = in_import
	return email


def target_exists(link_type: str, link_to: str) -> bool:
	return bool(link_to and frappe.db.exists(link_type, link_to))


class TestHostel360Desk(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_workspace_blocks_name_rows_that_exist(self):
		for name in WORKSPACES:
			doc = frappe.get_doc("Workspace", name)
			self.assertTrue(doc.public and doc.standard, name)

			labels = {table: {row.label for row in doc.get(table)} for table, _key in BLOCK_TABLES.values()}
			cards = {row.label for row in doc.links if row.type == "Card Break"}
			for block in json.loads(doc.content):
				if block["type"] in BLOCK_TABLES:
					table, key = BLOCK_TABLES[block["type"]]
					self.assertIn(block["data"][key], labels[table], f"{name}: {block}")
				elif block["type"] == "card":
					self.assertIn(block["data"]["card_name"], cards, f"{name}: {block}")
				elif block["type"] == "onboarding":
					self.assertTrue(frappe.db.exists("Module Onboarding", block["data"]["onboarding_name"]))

	def test_workspace_rows_point_at_existing_records(self):
		for name in WORKSPACES:
			doc = frappe.get_doc("Workspace", name)
			for row in doc.number_cards:
				self.assertTrue(target_exists("Number Card", row.number_card_name), f"{name}: {row.number_card_name}")
			for row in doc.charts:
				self.assertTrue(target_exists("Dashboard Chart", row.chart_name), f"{name}: {row.chart_name}")
			for row in doc.shortcuts:
				self.assertTrue(target_exists(row.type, row.link_to), f"{name}: {row.link_to}")
			for row in doc.links:
				if row.type == "Link":
					self.assertTrue(target_exists(row.link_type, row.link_to), f"{name}: {row.link_to}")

	def test_number_cards_evaluate(self):
		from frappe.desk.doctype.number_card.number_card import get_result

		for name in frappe.get_all("Number Card", filters={"is_standard": 1, "module": ("in", MODULES)}, pluck="name"):
			card = frappe.get_doc("Number Card", name)
			if card.type == "Custom":
				value = frappe.get_attr(card.method)(filters=card.filters_json)["value"]
			else:
				value = get_result(card.as_dict(), card.filters_json)
			self.assertGreaterEqual(value, 0, name)

	def test_dashboard_charts_evaluate(self):
		from frappe.desk.doctype.dashboard_chart.dashboard_chart import get as get_chart

		charts = frappe.get_all("Dashboard Chart", filters={"is_standard": 1, "module": ("in", MODULES)}, pluck="name")
		self.assertGreaterEqual(len(charts), 6)
		for name in charts:
			chart = frappe.get_doc("Dashboard Chart", name)
			if chart.chart_type == "Custom":
				method = f"hostel360.{frappe.scrub(chart.module)}.dashboard_chart_source.{frappe.scrub(chart.source)}.{frappe.scrub(chart.source)}.get"
				data = frappe.get_attr(method)(chart_name=name)
			else:
				data = get_chart(chart_name=name, refresh=1)
			# a group-by chart over an empty table answers None, which the widget shows as "no data"
			if data is not None:
				self.assertIn("labels", data, name)

	def test_sidebars_and_dock(self):
		from frappe.boot import build_entity_module_map, get_module_sidebars

		for shell in SHELLS:
			sidebar = frappe.get_doc("Sidebar", shell)
			self.assertEqual(sidebar.app, "hostel360")
			for item in sidebar.items:
				if item.type == "Link":
					self.assertTrue(target_exists(item.link_type, item.link_to), f"{shell}: {item.link_to}")

		dock = frappe.get_doc("Dock", "hostel360")
		self.assertEqual([row.link_to for row in dock.items], list(SHELLS))

		payload = get_module_sidebars()
		for shell in SHELLS:
			self.assertIn(shell, payload)
		owners = build_entity_module_map(payload)
		self.assertEqual(owners.get("Hostel"), "Hostel Admin")
		self.assertEqual(owners.get("Hostel Student"), "People and Rooms")
		self.assertEqual(owners.get("Hostel Fee Invoice"), "Fees")
		self.assertEqual(owners.get("Message Log"), "Messaging")

	def test_onboarding_steps_reference_real_doctypes(self):
		onboarding = frappe.get_doc("Module Onboarding", ONBOARDING)
		self.assertEqual(onboarding.module, "Hostel Setup")
		for row in onboarding.steps:
			step = frappe.get_doc("Onboarding Step", row.step)
			self.assertTrue(frappe.db.exists("DocType", step.reference_document), step.name)
			if step.action == "Update Settings" and step.field:
				self.assertTrue(frappe.get_meta(step.reference_document).has_field(step.field), step.name)

	def test_workspace_visibility_follows_roles(self):
		from frappe.desk.desktop import get_workspaces

		expected = {
			MESS_STAFF_ROLE: {"Hostel Admin", "Mess"},
			MESS_MANAGER_ROLE: {"Hostel Admin", "Mess", "Student Services"},
			OFFICE_ROLE: {"Hostel Admin", "People and Rooms", "Fees", "Student Services"},
			OPERATIONS_ROLE: {"Hostel Admin", "HyFib Operations"},
			AUDITOR_ROLE: set(WORKSPACES),
		}
		for role, workspaces in expected.items():
			frappe.set_user(make_user([role]))
			frappe.local.request_cache.clear()
			visible = {page["name"] for page in get_workspaces()["pages"]} & set(WORKSPACES)
			self.assertEqual(visible, workspaces, role)
			frappe.set_user("Administrator")
