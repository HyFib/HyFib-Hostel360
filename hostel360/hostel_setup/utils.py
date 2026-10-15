"""Small helpers shared by the Hostel Setup controllers."""

import re
from collections.abc import Iterator

import frappe
from frappe import _

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]*$")


def clean_code(value: str | None, label: str, max_length: int = 20) -> str:
	"""Upper-case a short code used inside document names (hostel, block, room, bed).

	Codes end up in names such as ``BH1-A-101-B``, so they are limited to letters, digits
	and hyphens; anything else would make names ambiguous or awkward in URLs.
	"""
	code = re.sub(r"\s+", "", (value or "")).upper()
	if not code:
		frappe.throw(_("{0} is required.").format(_(label)))
	if len(code) > max_length:
		frappe.throw(_("{0} can be at most {1} characters long.").format(_(label), max_length))
	if not CODE_RE.match(code):
		frappe.throw(
			_("{0} {1} may contain only letters, digits and hyphens.").format(_(label), frappe.bold(code))
		)
	return code


def bed_label_sequence() -> Iterator[str]:
	"""A, B, ... Z, AA, AB, ... in the order beds are numbered on the floor."""
	letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
	for letter in letters:
		yield letter
	for first in letters:
		for second in letters:
			yield first + second


def bed_label_key(label: str) -> tuple:
	"""Sort key that keeps B before AA (shorter labels first, then alphabetical)."""
	label = label or ""
	return (len(label), label)


def natural_key(value: str | None) -> list:
	"""Sort "G2" before "G10" and "101" before "1010"."""
	return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value or "")]


FLOOR_LABELS = {
	0: "Ground Floor",
	1: "First Floor",
	2: "Second Floor",
	3: "Third Floor",
	4: "Fourth Floor",
	5: "Fifth Floor",
	6: "Sixth Floor",
	7: "Seventh Floor",
	8: "Eighth Floor",
	9: "Ninth Floor",
	10: "Tenth Floor",
}


def default_floor_label(floor_number: int) -> str:
	if floor_number in FLOOR_LABELS:
		return FLOOR_LABELS[floor_number]
	if floor_number < 0:
		return f"Basement {abs(floor_number)}"
	return f"Floor {floor_number}"
