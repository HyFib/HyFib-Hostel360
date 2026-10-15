"""Mapping parsing and row building (no database needed)."""

import copy
import os

from frappe.tests import UnitTestCase

from hostel360.hostel_migration.mapping import MappingError, RowError, load_mapping

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")


def base_mapping() -> dict:
	return {
		"version": 1,
		"name": "Test mapping",
		"load_order": ["hostels", "rooms"],
		"entities": {
			"hostels": {
				"file": "hostels.csv",
				"doctype": "Hostel",
				"key": "id",
				"fields": {
					"hostel_code": {"column": "code", "transforms": ["upper"], "required": True},
					"hostel_name": "name",
				},
			},
			"rooms": {
				"file": "rooms.csv",
				"doctype": "Hostel Room",
				"key": ["hostel_id", "room_no"],
				"fields": {
					"hostel": {"column": "hostel_id", "lookup": "hostels", "required": True},
					"room_number": {"column": "room_no", "transforms": ["upper"]},
				},
			},
		},
	}


def no_lookup(spec, value):
	return f"<{spec.lookup.get('entity') or spec.lookup.get('doctype')}:{value}>"


class TestLoadMapping(UnitTestCase):
	def test_sample_mapping_is_valid(self):
		mapping = load_mapping(os.path.join(SAMPLES, "sample_mapping.json"))
		self.assertEqual(len(mapping.entities), 16)
		self.assertEqual(mapping.load_order[0], "academic_years")
		for entity in mapping.ordered_entities():
			self.assertTrue(os.path.isfile(os.path.join(SAMPLES, entity.file)), entity.file)

	def test_checksum_ignores_key_order(self):
		first = load_mapping(base_mapping())
		reordered = base_mapping()
		reordered["entities"] = dict(reversed(list(reordered["entities"].items())))
		self.assertEqual(first.checksum, load_mapping(reordered).checksum)
		changed = base_mapping()
		changed["entities"]["hostels"]["fields"]["hostel_name"] = "title"
		self.assertNotEqual(first.checksum, load_mapping(changed).checksum)

	def assertMappingError(self, mapping, fragment):
		with self.assertRaises(MappingError) as ctx:
			load_mapping(mapping)
		self.assertIn(fragment, str(ctx.exception))

	def test_structural_errors(self):
		self.assertMappingError("{not json", "not valid JSON")
		self.assertMappingError({"version": 2, "entities": {}}, "version 2")
		self.assertMappingError({"entities": {}}, "no entities")

		m = base_mapping()
		m["entities"]["hostels"]["colour"] = "blue"
		self.assertMappingError(m, "unknown keys colour")

		m = base_mapping()
		m["entities"]["hostels"]["file"] = "../etc/passwd.csv"
		self.assertMappingError(m, "inside the source folder")

		m = base_mapping()
		m["entities"]["hostels"]["file"] = "hostels.xls"
		self.assertMappingError(m, ".csv or .xlsx")

		m = base_mapping()
		m["entities"]["hostels"]["fields"]["hostel_name"] = {"column": "a", "value": "b"}
		self.assertMappingError(m, "exactly one of")

		m = base_mapping()
		m["entities"]["hostels"]["fields"]["hostel_name"] = {"column": "a", "transforms": ["shout"]}
		self.assertMappingError(m, "Unknown transform")

		m = base_mapping()
		m["entities"]["Hostels"] = m["entities"].pop("hostels")
		self.assertMappingError(m, "lower-case")

	def test_load_order_errors(self):
		m = base_mapping()
		m["load_order"] = ["rooms", "hostels"]
		self.assertMappingError(m, "loads later")

		m = base_mapping()
		m["load_order"] = ["hostels"]
		self.assertMappingError(m, "missing from load_order")

		m = base_mapping()
		m["load_order"] = ["hostels", "rooms", "beds"]
		self.assertMappingError(m, "unknown entities: beds")

		m = base_mapping()
		m["entities"]["rooms"]["fields"]["hostel"]["lookup"] = "blocks"
		self.assertMappingError(m, "unknown entity 'blocks'")

	def test_child_entity_requirements(self):
		m = base_mapping()
		m["entities"]["rooms"]["mode"] = "child"
		self.assertMappingError(m, "parent.entity and parent.column")
		m["entities"]["rooms"]["parent"] = {"entity": "hostels", "column": "hostel_id"}
		self.assertMappingError(m, "table field name")

	def test_match_on_must_be_mapped(self):
		m = base_mapping()
		m["entities"]["hostels"]["match_on"] = ["gender"]
		self.assertMappingError(m, "match_on fields gender")

	def test_entity_filter(self):
		mapping = load_mapping(base_mapping())
		self.assertEqual([e.name for e in mapping.ordered_entities(["rooms"])], ["rooms"])
		with self.assertRaises(MappingError):
			mapping.ordered_entities(["beds"])


class TestBuildRows(UnitTestCase):
	def entity(self, name="hostels", mutate=None):
		m = base_mapping()
		if mutate:
			mutate(m)
		return load_mapping(m).entities[name]

	def test_basic_row(self):
		built = self.entity().build({"id": "H1", "code": " bh1 ", "name": "Boys Hostel"}, no_lookup)
		self.assertEqual(built.values, {"hostel_code": "BH1", "hostel_name": "Boys Hostel"})

	def test_composite_key_and_lookup(self):
		rooms = self.entity("rooms")
		row = {"hostel_id": "H1", "room_no": "a101"}
		self.assertEqual(rooms.source_id(row), "H1|a101")
		built = rooms.build(row, no_lookup)
		self.assertEqual(built.values["hostel"], "<hostels:H1>")
		with self.assertRaises(RowError):
			rooms.source_id({"hostel_id": "H1", "room_no": " "})

	def test_required_field(self):
		with self.assertRaises(RowError) as ctx:
			self.entity().build({"id": "H1", "code": "", "name": "x"}, no_lookup)
		self.assertIn("hostel_code is required", str(ctx.exception))

	def test_transform_errors_name_the_field(self):
		def mutate(m):
			m["entities"]["hostels"]["fields"]["is_active"] = {"column": "active", "transforms": ["boolean"]}

		with self.assertRaises(RowError) as ctx:
			self.entity(mutate=mutate).build({"id": "H1", "code": "X", "name": "x", "active": "perhaps"}, no_lookup)
		self.assertTrue(str(ctx.exception).startswith("is_active:"))

	def test_defaults_constants_and_columns(self):
		def mutate(m):
			fields = m["entities"]["hostels"]["fields"]
			fields["gender"] = {"column": "type", "value_map": {"B": "Male"}, "default": "Mixed"}
			fields["is_active"] = {"value": 1}
			fields["address"] = {"columns": ["street", "town"], "separator": ", "}

		built = self.entity(mutate=mutate).build(
			{"id": "H1", "code": "X", "name": "x", "type": "", "street": "1 Main Rd", "town": "Karaikudi"}, no_lookup
		)
		self.assertEqual(built.values["gender"], "Mixed")
		self.assertEqual(built.values["is_active"], 1)
		self.assertEqual(built.values["address"], "1 Main Rd, Karaikudi")

	def test_templates_resolve_after_fields_in_any_order(self):
		def mutate(m):
			m["entities"]["rooms"]["fields"] = {
				"name_check": {"template": "{hostel}-{room_number}"},
				"room_number": {"column": "room_no", "transforms": ["upper"]},
				"hostel": {"column": "hostel_id", "lookup": "hostels"},
			}

		built = self.entity("rooms", mutate).build({"hostel_id": "H1", "room_no": "a1"}, no_lookup)
		self.assertEqual(built.values["name_check"], "<hostels:H1>-A1")
		self.assertEqual(list(built.values), ["name_check", "room_number", "hostel"])

	def test_template_missing_value(self):
		def mutate(m):
			m["entities"]["hostels"]["fields"]["remarks"] = {"template": "Legacy {legacy_no}"}

		with self.assertRaises(RowError):
			self.entity(mutate=mutate).build({"id": "H1", "code": "X", "name": "x", "legacy_no": ""}, no_lookup)

	def test_child_rows_and_when(self):
		def mutate(m):
			m["entities"]["hostels"]["children"] = {
				"facilities": [
					{"facility": {"value": "Cot"}},
					{"$when": "fan", "facility": {"value": "Ceiling Fan"}},
					{"$when": "wifi", "facility": {"value": "Wi-Fi"}},
				],
				"items": [{"fee_head": {"value": "Rent"}, "amount": {"column": "rent", "transforms": ["decimal"]}}],
			}

		entity = self.entity(mutate=mutate)
		built = entity.build({"id": "H1", "code": "X", "name": "x", "fan": "Y", "wifi": "N", "rent": "0"}, no_lookup)
		self.assertEqual([r["facility"] for r in built.children["facilities"]], ["Cot", "Ceiling Fan"])
		# A row whose only source column is zero is left out.
		self.assertEqual(built.children["items"], [])
		built = entity.build({"id": "H1", "code": "X", "name": "x", "fan": "", "wifi": "yes", "rent": "1,200"}, no_lookup)
		self.assertEqual([r["facility"] for r in built.children["facilities"]], ["Cot", "Wi-Fi"])
		self.assertEqual(built.children["items"], [{"fee_head": "Rent", "amount": 1200.0}])

	def test_filter(self):
		def mutate(m):
			m["entities"]["hostels"]["filter"] = {"column": "deleted", "not_in": ["Y"]}

		entity = self.entity(mutate=mutate)
		self.assertTrue(entity.is_included({"deleted": "N"}))
		self.assertTrue(entity.is_included({"deleted": ""}))
		self.assertFalse(entity.is_included({"deleted": " y "}))

	def test_null_values(self):
		def mutate(m):
			m["defaults"] = {"null_values": ["NIL"]}
			m["entities"]["hostels"]["fields"]["address"] = "address"

		built = self.entity(mutate=mutate).build({"id": "H1", "code": "X", "name": "x", "address": "nil"}, no_lookup)
		self.assertIsNone(built.values["address"])

	def test_checksum_follows_values(self):
		entity = self.entity()
		row = {"id": "H1", "code": "bh1", "name": "Boys Hostel"}
		first = entity.build(row, no_lookup).checksum()
		self.assertEqual(first, entity.build(copy.deepcopy(row), no_lookup).checksum())
		# Cosmetic differences that map to the same value do not count as a change.
		self.assertEqual(first, entity.build({**row, "code": " BH1 "}, no_lookup).checksum())
		self.assertNotEqual(first, entity.build({**row, "name": "Boys Hostel I"}, no_lookup).checksum())
		self.assertNotEqual(first, entity.build(row, no_lookup).checksum({"photo": "abc"}))

	def test_referenced_columns(self):
		rooms = self.entity("rooms")
		self.assertEqual(rooms.referenced_columns(), {"hostel_id", "room_no"})
