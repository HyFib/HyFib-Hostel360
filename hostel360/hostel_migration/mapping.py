"""Migration mapping: how a legacy export becomes Hostel360 records.

A mapping is a JSON document (format in ``README.md`` next to this file). ``load_mapping``
parses and checks its structure without touching the database, so a broken mapping is
reported before any row is read. ``check_against_meta`` then checks DocTypes and field
names against the site. ``EntitySpec.build`` turns one source row into the values for
one document.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from hostel360.hostel_migration.transforms import (
	DEFAULT_DATE_FORMATS,
	DEFAULT_DATETIME_FORMATS,
	DEFAULT_NULL_VALUES,
	FALSE_WORDS,
	TransformError,
	apply_transforms,
	apply_value_map,
	clean_null,
	is_empty,
	normalise_transform,
	to_text,
)

MAPPING_VERSION = 1
ENTITY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
SOURCE_SYSTEM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,59}$")
TEMPLATE_RE = re.compile(r"\{([^{}]+)\}")
SUPPORTED_EXTENSIONS = (".csv", ".xlsx")
MAX_SOURCE_ID_LENGTH = 140

FIELD_KEYS = frozenset(
	{
		"column",
		"columns",
		"separator",
		"value",
		"template",
		"default",
		"transforms",
		"value_map",
		"strict",
		"lookup",
		"required",
		"attachment",
	}
)
ENTITY_KEYS = frozenset(
	{
		"file",
		"sheet",
		"doctype",
		"mode",
		"key",
		"match_on",
		"fields",
		"children",
		"submit",
		"filter",
		"parent",
		"table",
		"match_field",
		"reconcile",
		"on_submitted_change",
		"docname",
		"encoding",
		"delimiter",
		"date_formats",
		"datetime_formats",
		"description",
	}
)


class MappingError(ValueError):
	"""The mapping itself is wrong; nothing can be migrated until it is fixed."""


class RowError(ValueError):
	"""One source row cannot be migrated. The run carries on with the next row."""


@dataclass
class FieldSpec:
	target: str
	column: str | None = None
	columns: list[str] | None = None
	separator: str = " "
	value: Any = None
	has_value: bool = False
	template: str | None = None
	default: Any = None
	transforms: list = field(default_factory=list)
	value_map: dict | None = None
	strict: bool = False
	lookup: dict | None = None
	required: bool = False
	attachment: bool = False

	@property
	def source_columns(self) -> list[str]:
		if self.column:
			return [self.column]
		return list(self.columns or [])


@dataclass
class ChildRowSpec:
	fields: list[FieldSpec]
	when: str | None = None


@dataclass
class TotalSpec:
	label: str
	source_column: str
	target_field: str
	transforms: list = field(default_factory=lambda: ["decimal"])


@dataclass
class EntitySpec:
	name: str
	file: str
	doctype: str
	key: list[str]
	fields: list[FieldSpec]
	mode: str = "document"
	sheet: str | None = None
	match_on: list[str] = field(default_factory=list)
	children: dict[str, list[ChildRowSpec]] = field(default_factory=dict)
	submit: bool = False
	filter: dict | None = None
	parent: dict | None = None
	table: str | None = None
	match_field: str | None = None
	totals: list[TotalSpec] = field(default_factory=list)
	on_submitted_change: str = "fail"
	docname: FieldSpec | None = None
	encoding: str | None = None
	delimiter: str | None = None
	context: dict = field(default_factory=dict)
	null_values: tuple = DEFAULT_NULL_VALUES

	# ------------------------------------------------------------------ helpers

	def referenced_columns(self) -> set[str]:
		columns = set(self.key)
		specs = list(self.fields)
		for rows in self.children.values():
			for row in rows:
				specs.extend(row.fields)
				if row.when:
					columns.add(row.when)
		if self.docname:
			specs.append(self.docname)
		for spec in specs:
			columns.update(spec.source_columns)
			if spec.template:
				# Template names may be target fields; only names that are not are columns.
				targets = {s.target for s in specs}
				columns.update(name for name in TEMPLATE_RE.findall(spec.template) if name not in targets)
		if self.filter:
			columns.add(self.filter["column"])
		if self.parent:
			columns.add(self.parent["column"])
		for total in self.totals:
			columns.add(total.source_column)
		return columns

	def lookup_entities(self) -> set[str]:
		names = set()
		specs = list(self.fields)
		for rows in self.children.values():
			for row in rows:
				specs.extend(row.fields)
		for spec in specs:
			if spec.lookup and spec.lookup.get("entity"):
				names.add(spec.lookup["entity"])
		if self.parent:
			names.add(self.parent["entity"])
		return names

	def source_id(self, row: dict) -> str:
		parts = [to_text(get_cell(row, column)).strip() for column in self.key]
		if any(not part for part in parts):
			missing = [column for column, part in zip(self.key, parts, strict=True) if not part]
			raise RowError(f"The key column {', '.join(missing)} is empty.")
		source_id = "|".join(parts)
		if len(source_id) > MAX_SOURCE_ID_LENGTH:
			raise RowError(
				f"The legacy ID '{source_id[:40]}...' is longer than {MAX_SOURCE_ID_LENGTH} characters."
			)
		return source_id

	def is_included(self, row: dict) -> bool:
		"""Rows the entity's filter leaves out (soft-deleted, test records...)."""
		if not self.filter:
			return True
		value = to_text(get_cell(row, self.filter["column"])).strip().lower()
		if "in" in self.filter:
			return value in {to_text(v).strip().lower() for v in self.filter["in"]}
		if "not_in" in self.filter:
			return value not in {to_text(v).strip().lower() for v in self.filter["not_in"]}
		if "equals" in self.filter:
			return value == to_text(self.filter["equals"]).strip().lower()
		if "not_empty" in self.filter:
			return bool(value) == bool(self.filter["not_empty"])
		return True

	def build(self, row: dict, resolve_lookup) -> "BuiltRow":
		"""Map one source row. ``resolve_lookup(spec, value) -> name`` resolves lookups."""
		values: dict[str, Any] = {}
		attachments: dict[str, str] = {}
		# Templates may use other mapped fields ("{room}-{bed_no}"), so they are filled
		# after every plain field, whatever order the JSON lists them in.
		ordered = [s for s in self.fields if not s.template] + [s for s in self.fields if s.template]
		for spec in ordered:
			value = self.resolve_field(spec, row, values, resolve_lookup)
			if spec.attachment:
				if value is not None:
					attachments[spec.target] = to_text(value).strip()
				continue
			values[spec.target] = value
		values = {spec.target: values[spec.target] for spec in self.fields if spec.target in values}

		children: dict[str, list[dict]] = {}
		for table, row_specs in self.children.items():
			table_rows = []
			for child in row_specs:
				if child.when is not None:
					if not _is_present(clean_null(get_cell(row, child.when), self.null_values)):
						continue
				elif not any(
					_is_present(clean_null(get_cell(row, column), self.null_values))
					for spec in child.fields
					for column in spec.source_columns
				) and any(spec.source_columns for spec in child.fields):
					continue
				child_values: dict[str, Any] = {}
				for spec in [s for s in child.fields if not s.template] + [
					s for s in child.fields if s.template
				]:
					child_values[spec.target] = self.resolve_field(
						spec, row, {**values, **child_values}, resolve_lookup
					)
				table_rows.append({spec.target: child_values[spec.target] for spec in child.fields})
			children[table] = table_rows

		docname = None
		if self.docname:
			docname = self.resolve_field(self.docname, row, values, resolve_lookup)
			docname = to_text(docname).strip() if docname is not None else None
		return BuiltRow(values=values, children=children, attachments=attachments, docname=docname)

	def resolve_field(self, spec: FieldSpec, row: dict, resolved: dict, resolve_lookup):
		try:
			if spec.has_value:
				return spec.value
			if spec.template:
				raw = render_template(spec.template, resolved, row)
			elif spec.columns:
				parts = [
					to_text(clean_null(get_cell(row, c), self.null_values)).strip() for c in spec.columns
				]
				raw = spec.separator.join(part for part in parts if part)
			else:
				raw = get_cell(row, spec.column)
			raw = clean_null(raw, self.null_values)

			if is_empty(raw):
				value = spec.default
			else:
				value = apply_transforms(raw, spec.transforms, self.context)
				value = apply_value_map(value, spec.value_map, spec.strict)
				if spec.lookup and not is_empty(value):
					value = resolve_lookup(spec, value)
				if is_empty(value):
					value = spec.default
		except TransformError as e:
			raise RowError(f"{spec.target}: {e}") from e

		if spec.required and is_empty(value):
			source = ", ".join(spec.source_columns) or spec.template or spec.target
			raise RowError(f"{spec.target} is required but {source} is empty.")
		return value

	def total_value(self, total: TotalSpec, row: dict):
		raw = clean_null(get_cell(row, total.source_column), self.null_values)
		if is_empty(raw):
			return None
		return apply_transforms(raw, total.transforms, self.context)


@dataclass
class BuiltRow:
	values: dict
	children: dict
	attachments: dict
	docname: str | None = None

	def checksum(self, attachment_hashes: dict | None = None) -> str:
		payload = {
			"values": self.values,
			"children": self.children,
			"attachments": attachment_hashes or self.attachments,
			"docname": self.docname,
		}
		blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
		return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class Mapping:
	name: str
	source_system: str
	version: int
	entities: dict[str, EntitySpec]
	load_order: list[str]
	attachments_dir: str
	checksum: str
	raw: dict

	def ordered_entities(self, only: list[str] | None = None) -> list[EntitySpec]:
		names = list(self.load_order)
		if only:
			unknown = sorted(set(only) - set(self.entities))
			if unknown:
				raise MappingError(f"Unknown entities: {', '.join(unknown)}. Known: {', '.join(names)}.")
			names = [name for name in names if name in set(only)]
		return [self.entities[name] for name in names]


# --------------------------------------------------------------------- utilities


def get_cell(row: dict, column: str | None):
	if column is None:
		return None
	if column in row:
		return row[column]
	folded = column.strip().lower()
	for key, value in row.items():
		if key.strip().lower() == folded:
			return value
	return None


def _is_present(value) -> bool:
	"""Does a cell switch a child row on? Empty, zero and no-words ("N", "No") do not."""
	if is_empty(value):
		return False
	text = to_text(value).strip().replace(",", "")
	if text.lower() in FALSE_WORDS:
		return False
	try:
		return float(text) != 0
	except ValueError:
		return True


def render_template(template: str, resolved: dict, row: dict) -> str:
	"""Fill ``{name}`` from mapped values first, then from source columns."""

	def substitute(match):
		name = match.group(1).strip()
		if name in resolved and not is_empty(resolved[name]):
			return to_text(resolved[name]).strip()
		cell = get_cell(row, name)
		if not is_empty(cell):
			return to_text(cell).strip()
		raise TransformError(f"the template needs '{name}', which is empty.")

	return TEMPLATE_RE.sub(substitute, template)


def slugify(text: str) -> str:
	return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60]


def _as_list(value, label: str) -> list:
	if value is None:
		return []
	if isinstance(value, str):
		return [value]
	if isinstance(value, list) and all(isinstance(v, str) for v in value):
		return value
	raise MappingError(f"{label} must be a column name or a list of column names.")


def parse_field(target: str, spec, where: str) -> FieldSpec:
	if isinstance(spec, str):
		return FieldSpec(target=target, column=spec)
	if not isinstance(spec, dict):
		raise MappingError(f"{where}.{target}: a field must be a column name or an object.")
	unknown = set(spec) - FIELD_KEYS
	if unknown:
		raise MappingError(f"{where}.{target}: unknown keys {', '.join(sorted(unknown))}.")

	sources = [key for key in ("column", "columns", "value", "template") if key in spec]
	if len(sources) != 1:
		raise MappingError(f"{where}.{target}: give exactly one of column, columns, value or template.")

	transforms = spec.get("transforms") or []
	if not isinstance(transforms, list):
		raise MappingError(f"{where}.{target}: transforms must be a list.")
	for transform in transforms:
		try:
			normalise_transform(transform)
		except ValueError as e:
			raise MappingError(f"{where}.{target}: {e}") from e

	value_map = spec.get("value_map")
	if value_map is not None and not isinstance(value_map, dict):
		raise MappingError(f"{where}.{target}: value_map must be an object.")

	lookup = spec.get("lookup")
	if lookup is not None:
		if isinstance(lookup, str):
			lookup = {"entity": lookup}
		if not isinstance(lookup, dict) or not (
			lookup.get("entity") or (lookup.get("doctype") and lookup.get("field"))
		):
			raise MappingError(
				f"{where}.{target}: lookup must name an entity, or a doctype and the field to match on."
			)

	columns = spec.get("columns")
	if columns is not None:
		columns = _as_list(columns, f"{where}.{target}.columns")

	return FieldSpec(
		target=target,
		column=spec.get("column"),
		columns=columns,
		separator=spec.get("separator", " "),
		value=spec.get("value"),
		has_value="value" in spec,
		template=spec.get("template"),
		default=spec.get("default"),
		transforms=transforms,
		value_map=value_map,
		strict=bool(spec.get("strict")),
		lookup=lookup,
		required=bool(spec.get("required")),
		attachment=bool(spec.get("attachment")),
	)


def parse_fields(fields, where: str) -> list[FieldSpec]:
	if not isinstance(fields, dict) or not fields:
		raise MappingError(f"{where}: fields must be a non-empty object of target field -> source.")
	return [parse_field(target, spec, where) for target, spec in fields.items()]


def parse_entity(name: str, spec: dict, defaults: dict) -> EntitySpec:
	where = f"entities.{name}"
	if not ENTITY_NAME_RE.match(name):
		raise MappingError(f"{where}: entity names use lower-case letters, digits and underscores.")
	if not isinstance(spec, dict):
		raise MappingError(f"{where}: must be an object.")
	unknown = set(spec) - ENTITY_KEYS
	if unknown:
		raise MappingError(f"{where}: unknown keys {', '.join(sorted(unknown))}.")
	for required in ("file", "doctype", "key", "fields"):
		if not spec.get(required):
			raise MappingError(f"{where}: '{required}' is required.")

	file_name = spec["file"]
	if not isinstance(file_name, str) or not file_name.lower().endswith(SUPPORTED_EXTENSIONS):
		raise MappingError(f"{where}: file must be a .csv or .xlsx file name.")
	if file_name.startswith(("/", "\\")) or ".." in re.split(r"[\\/]", file_name):
		raise MappingError(f"{where}: file must be a name inside the source folder.")

	mode = spec.get("mode", "document")
	if mode not in ("document", "child"):
		raise MappingError(f"{where}: mode must be 'document' or 'child'.")

	fields = parse_fields(spec["fields"], where)
	children: dict[str, list[ChildRowSpec]] = {}
	for table, rows in (spec.get("children") or {}).items():
		if isinstance(rows, dict):
			rows = [rows]
		if not isinstance(rows, list) or not rows:
			raise MappingError(f"{where}.children.{table}: give a list of row templates.")
		parsed_rows = []
		for idx, row in enumerate(rows, start=1):
			if not isinstance(row, dict):
				raise MappingError(f"{where}.children.{table}[{idx}]: a row template must be an object.")
			row = dict(row)
			when = row.pop("$when", None)
			parsed_rows.append(
				ChildRowSpec(fields=parse_fields(row, f"{where}.children.{table}[{idx}]"), when=when)
			)
		children[table] = parsed_rows

	parent = spec.get("parent")
	if mode == "child":
		if not isinstance(parent, dict) or not parent.get("entity") or not parent.get("column"):
			raise MappingError(f"{where}: a child entity needs parent.entity and parent.column.")
		if not spec.get("table"):
			raise MappingError(f"{where}: a child entity needs the parent's table field name.")
		if spec.get("submit") or spec.get("children") or spec.get("docname"):
			raise MappingError(f"{where}: submit, children and docname do not apply to a child entity.")

	filter_spec = spec.get("filter")
	if filter_spec is not None:
		if not isinstance(filter_spec, dict) or not filter_spec.get("column"):
			raise MappingError(f"{where}.filter: needs a column and one of in, not_in, equals, not_empty.")
		if not {"in", "not_in", "equals", "not_empty"} & set(filter_spec):
			raise MappingError(f"{where}.filter: needs one of in, not_in, equals, not_empty.")

	totals = []
	for idx, total in enumerate(spec.get("reconcile") or [], start=1):
		if not isinstance(total, dict) or not total.get("source_column") or not total.get("target_field"):
			raise MappingError(f"{where}.reconcile[{idx}]: needs source_column and target_field.")
		transforms = total.get("transforms") or ["decimal"]
		for transform in transforms:
			try:
				normalise_transform(transform)
			except ValueError as e:
				raise MappingError(f"{where}.reconcile[{idx}]: {e}") from e
		totals.append(
			TotalSpec(
				label=total.get("label") or total["target_field"],
				source_column=total["source_column"],
				target_field=total["target_field"],
				transforms=transforms,
			)
		)
	if totals and mode == "child":
		raise MappingError(f"{where}: reconcile totals are not supported for child entities.")

	on_change = spec.get("on_submitted_change", "fail")
	if on_change not in ("fail", "skip"):
		raise MappingError(f"{where}: on_submitted_change must be 'fail' or 'skip'.")

	context = {
		"date_formats": spec.get("date_formats")
		or defaults.get("date_formats")
		or list(DEFAULT_DATE_FORMATS),
		"datetime_formats": spec.get("datetime_formats")
		or defaults.get("datetime_formats")
		or list(DEFAULT_DATETIME_FORMATS),
	}
	null_values = tuple(defaults.get("null_values") or DEFAULT_NULL_VALUES)

	docname = None
	if spec.get("docname") is not None:
		docname = parse_field("docname", spec["docname"], where)

	entity = EntitySpec(
		name=name,
		file=file_name,
		sheet=spec.get("sheet"),
		doctype=spec["doctype"],
		key=_as_list(spec["key"], f"{where}.key"),
		fields=fields,
		mode=mode,
		match_on=_as_list(spec.get("match_on"), f"{where}.match_on"),
		children=children,
		submit=bool(spec.get("submit")),
		filter=filter_spec,
		parent=parent,
		table=spec.get("table"),
		match_field=spec.get("match_field"),
		totals=totals,
		on_submitted_change=on_change,
		docname=docname,
		encoding=spec.get("encoding") or defaults.get("encoding"),
		delimiter=spec.get("delimiter") or defaults.get("delimiter"),
		context=context,
		null_values=null_values,
	)
	targets = {f.target for f in fields}
	missing_match = [name for name in entity.match_on if name not in targets]
	if missing_match:
		raise MappingError(f"{where}: match_on fields {', '.join(missing_match)} are not mapped.")
	if entity.match_field and entity.match_field not in targets:
		raise MappingError(f"{where}: match_field {entity.match_field} is not mapped.")
	return entity


def load_mapping(source) -> Mapping:
	"""Parse a mapping from a JSON string, a dict, or a path to a JSON file."""
	if isinstance(source, dict):
		raw = source
		text = json.dumps(source, sort_keys=True)
	else:
		text = str(source)
		if not text.lstrip().startswith("{"):
			try:
				with open(text, encoding="utf-8") as f:
					text = f.read()
			except OSError as e:
				raise MappingError(f"Cannot read the mapping file {source}: {e.strerror}.") from e
		try:
			raw = json.loads(text)
		except json.JSONDecodeError as e:
			raise MappingError(
				f"The mapping is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}."
			) from e
	if not isinstance(raw, dict):
		raise MappingError("The mapping must be a JSON object.")

	version = raw.get("version", MAPPING_VERSION)
	if version != MAPPING_VERSION:
		raise MappingError(f"Mapping version {version} is not supported (expected {MAPPING_VERSION}).")

	defaults = raw.get("defaults") or {}
	if not isinstance(defaults, dict):
		raise MappingError("defaults must be an object.")
	entities_raw = raw.get("entities")
	if not isinstance(entities_raw, dict) or not entities_raw:
		raise MappingError("The mapping has no entities.")

	entities = {name: parse_entity(name, spec, defaults) for name, spec in entities_raw.items()}

	load_order = raw.get("load_order") or list(entities_raw)
	if not isinstance(load_order, list) or not all(isinstance(n, str) for n in load_order):
		raise MappingError("load_order must be a list of entity names.")
	unknown = [name for name in load_order if name not in entities]
	if unknown:
		raise MappingError(f"load_order names unknown entities: {', '.join(unknown)}.")
	if len(set(load_order)) != len(load_order):
		raise MappingError("load_order lists an entity more than once.")
	missing = [name for name in entities if name not in load_order]
	if missing:
		raise MappingError(f"Entities missing from load_order: {', '.join(missing)}.")

	position = {name: idx for idx, name in enumerate(load_order)}
	for entity in entities.values():
		for dependency in entity.lookup_entities():
			if dependency not in entities:
				raise MappingError(f"entities.{entity.name}: looks up unknown entity '{dependency}'.")
			if position[dependency] > position[entity.name]:
				raise MappingError(
					f"entities.{entity.name}: looks up '{dependency}', which loads later. Move it earlier in load_order."
				)

	source_system = raw.get("source_system") or slugify(raw.get("name") or "")
	if not isinstance(source_system, str) or not SOURCE_SYSTEM_RE.match(source_system):
		raise MappingError(
			"source_system must be a short lower-case name such as 'legacy-hostel-register' "
			"(letters, digits, dot, dash, underscore)."
		)

	attachments_dir = raw.get("attachments_dir") or "attachments"
	if attachments_dir.startswith(("/", "\\")) or ".." in re.split(r"[\\/]", attachments_dir):
		raise MappingError("attachments_dir must be a folder inside the source folder.")

	canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
	return Mapping(
		name=str(raw.get("name") or "Legacy data"),
		source_system=source_system,
		version=version,
		entities=entities,
		load_order=load_order,
		attachments_dir=attachments_dir,
		checksum=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
		raw=raw,
	)


def check_against_meta(mapping: Mapping) -> list[str]:
	"""Problems with DocTypes and field names on this site. Empty list = all good."""
	import frappe

	problems = []
	for entity in mapping.entities.values():
		where = f"entities.{entity.name}"
		if not frappe.db.exists("DocType", entity.doctype):
			problems.append(f"{where}: DocType {entity.doctype} does not exist on this site.")
			continue
		meta = frappe.get_meta(entity.doctype)
		if meta.issingle or meta.istable:
			problems.append(
				f"{where}: {entity.doctype} is a single or child DocType; map its parent instead."
			)
			continue

		if entity.mode == "child":
			table_field = meta.get_field(entity.table)
			if not table_field or table_field.fieldtype not in ("Table", "Table MultiSelect"):
				problems.append(f"{where}: {entity.doctype} has no table field {entity.table}.")
				continue
			problems.extend(_check_fields(frappe.get_meta(table_field.options), entity.fields, where))
			continue

		problems.extend(_check_fields(meta, entity.fields, where))
		if entity.submit and not meta.is_submittable:
			problems.append(f"{where}: submit is set but {entity.doctype} is not submittable.")
		for table, rows in entity.children.items():
			table_field = meta.get_field(table)
			if not table_field or table_field.fieldtype not in ("Table", "Table MultiSelect"):
				problems.append(f"{where}.children: {entity.doctype} has no table field {table}.")
				continue
			child_meta = frappe.get_meta(table_field.options)
			for row in rows:
				problems.extend(_check_fields(child_meta, row.fields, f"{where}.children.{table}"))
		for total in entity.totals:
			if not meta.has_field(total.target_field):
				problems.append(f"{where}.reconcile: {entity.doctype} has no field {total.target_field}.")
		for spec in entity.fields:
			if spec.attachment:
				df = meta.get_field(spec.target)
				if df and df.fieldtype not in ("Attach", "Attach Image"):
					problems.append(
						f"{where}.{spec.target}: attachments need an Attach or Attach Image field."
					)
	return problems


def _check_fields(meta, specs: list[FieldSpec], where: str) -> list[str]:
	from frappe.model import default_fields

	problems = []
	for spec in specs:
		if spec.target in default_fields and spec.target not in ("name",):
			continue
		if not meta.has_field(spec.target):
			problems.append(f"{where}: {meta.name} has no field {spec.target}.")
		if spec.lookup and spec.lookup.get("doctype"):
			import frappe

			if not frappe.db.exists("DocType", spec.lookup["doctype"]):
				problems.append(
					f"{where}.{spec.target}: lookup DocType {spec.lookup['doctype']} does not exist."
				)
	return problems
