"""Reading legacy export files.

Exports arrive as CSV (often saved from Excel on Windows, so UTF-8 with a BOM or
Windows-1252) or as XLSX workbooks. Both are read into ``(line_number, {header: value})``
pairs; line numbers match what the person sees in Excel (the header is line 1) so the
report can point at the exact row to fix.
"""

import csv
import hashlib
import io
import os
from dataclasses import dataclass

from hostel360.hostel_migration.mapping import MappingError
from hostel360.hostel_migration.transforms import is_empty

FALLBACK_ENCODINGS = ("utf-8-sig", "cp1252")
SNIFF_DELIMITERS = ",;\t|"


@dataclass
class SourceTable:
	path: str
	headers: list[str]
	rows: list[tuple[int, dict]]
	checksum: str
	encoding: str | None = None


def resolve_inside(base_dir: str, relative: str) -> str:
	"""Join ``relative`` to ``base_dir`` and refuse anything that escapes it."""
	base = os.path.realpath(base_dir)
	path = os.path.realpath(os.path.join(base, relative))
	if path != base and not path.startswith(base + os.sep):
		raise MappingError(f"{relative} points outside the source folder.")
	return path


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
	digest = hashlib.sha256()
	with open(path, "rb") as f:
		while chunk := f.read(chunk_size):
			digest.update(chunk)
	return digest.hexdigest()


def read_table(
	source_dir: str,
	file_name: str,
	sheet: str | None = None,
	encoding: str | None = None,
	delimiter: str | None = None,
) -> SourceTable:
	path = resolve_inside(source_dir, file_name)
	if not os.path.isfile(path):
		raise MappingError(f"Source file {file_name} was not found in {source_dir}.")
	if path.lower().endswith(".xlsx"):
		return _read_xlsx(path, sheet)
	return _read_csv(path, encoding, delimiter)


def _clean_headers(raw_headers, path: str) -> list[str]:
	headers = [("" if h is None else str(h)).strip().lstrip("﻿") for h in raw_headers]
	while headers and not headers[-1]:
		headers.pop()
	if not headers:
		raise MappingError(f"{os.path.basename(path)} has no header row.")
	seen = set()
	for header in headers:
		if not header:
			raise MappingError(f"{os.path.basename(path)} has a column without a heading.")
		folded = header.lower()
		if folded in seen:
			raise MappingError(f"{os.path.basename(path)} has the column '{header}' twice.")
		seen.add(folded)
	return headers


def _row_dict(headers: list[str], values) -> dict | None:
	values = list(values)
	if all(is_empty(v) for v in values):
		return None
	values += [None] * (len(headers) - len(values))
	return {header: values[idx] for idx, header in enumerate(headers)}


def _decode(data: bytes, encoding: str | None) -> tuple[str, str]:
	if encoding:
		try:
			return data.decode(encoding), encoding
		except (UnicodeDecodeError, LookupError) as e:
			raise MappingError(f"The file is not readable as {encoding}: {e}.") from e
	for candidate in FALLBACK_ENCODINGS:
		try:
			return data.decode(candidate), candidate
		except UnicodeDecodeError:
			continue
	raise MappingError("The file is neither UTF-8 nor Windows-1252 text. Save it again as CSV UTF-8.")


def _read_csv(path: str, encoding: str | None, delimiter: str | None) -> SourceTable:
	with open(path, "rb") as f:
		data = f.read()
	checksum = hashlib.sha256(data).hexdigest()
	text, used_encoding = _decode(data, encoding)
	if "\x00" in text:
		raise MappingError(f"{os.path.basename(path)} contains binary data; it does not look like a CSV file.")

	if not delimiter:
		sample = text[:20000]
		try:
			delimiter = csv.Sniffer().sniff(sample.split("\n", 1)[0], delimiters=SNIFF_DELIMITERS).delimiter
		except csv.Error:
			delimiter = ","

	reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
	try:
		raw_headers = next(reader)
	except StopIteration as e:
		raise MappingError(f"{os.path.basename(path)} is empty.") from e
	headers = _clean_headers(raw_headers, path)

	rows = []
	try:
		for record in reader:
			# reader.line_num counts physical lines, which is right for quoted multi-line cells.
			if len(record) > len(headers) and any(not is_empty(v) for v in record[len(headers) :]):
				raise MappingError(
					f"{os.path.basename(path)} line {reader.line_num} has more values than there are columns."
				)
			row = _row_dict(headers, record[: len(headers)])
			if row is not None:
				rows.append((reader.line_num, row))
	except csv.Error as e:
		raise MappingError(f"{os.path.basename(path)} line {reader.line_num}: {e}.") from e
	return SourceTable(path=path, headers=headers, rows=rows, checksum=checksum, encoding=used_encoding)


def _read_xlsx(path: str, sheet: str | None) -> SourceTable:
	from openpyxl import load_workbook

	checksum = sha256_file(path)
	try:
		workbook = load_workbook(path, read_only=True, data_only=True)
	except Exception as e:
		raise MappingError(f"{os.path.basename(path)} is not a readable Excel workbook: {e}.") from e
	try:
		if sheet:
			if sheet not in workbook.sheetnames:
				raise MappingError(
					f"{os.path.basename(path)} has no sheet '{sheet}' (sheets: {', '.join(workbook.sheetnames)})."
				)
			worksheet = workbook[sheet]
		else:
			worksheet = workbook.worksheets[0]

		headers = None
		rows = []
		for line_number, values in enumerate(worksheet.iter_rows(values_only=True), start=1):
			if headers is None:
				if all(is_empty(v) for v in values):
					continue
				headers = _clean_headers(values, path)
				continue
			row = _row_dict(headers, values[: len(headers)])
			if row is not None:
				rows.append((line_number, row))
		if headers is None:
			raise MappingError(f"{os.path.basename(path)} is empty.")
	finally:
		workbook.close()
	return SourceTable(path=path, headers=headers, rows=rows, checksum=checksum)


def missing_columns(table: SourceTable, columns: set[str]) -> list[str]:
	present = {h.lower() for h in table.headers}
	return sorted(c for c in columns if c and c.lower() not in present)
