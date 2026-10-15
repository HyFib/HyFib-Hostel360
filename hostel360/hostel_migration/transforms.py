"""Value transforms used by migration mappings.

Legacy exports are messy: dates in three formats in the same column, amounts written as
"Rs. 12,500/-", mobile numbers with +91 or a leading zero, "Y"/"Active"/"1" for the same
flag. Each transform turns one raw cell into the value Hostel360 stores, or raises
``TransformError`` with a message a data-entry clerk can act on.

Transforms are pure functions (``value, params, context -> value``) so they can be tested
without a database. They are only called for non-empty values; empty cells are handled by
the mapping (defaults, required checks) before and after.
"""

import datetime
import re
from collections.abc import Callable, Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

DEFAULT_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y")
DEFAULT_DATETIME_FORMATS = (
	"%d-%m-%Y %H:%M:%S",
	"%d-%m-%Y %H:%M",
	"%d/%m/%Y %H:%M:%S",
	"%d/%m/%Y %H:%M",
	"%Y-%m-%d %H:%M:%S",
	"%Y-%m-%d %H:%M",
	"%Y-%m-%dT%H:%M:%S",
	"%d-%m-%Y %I:%M %p",
	"%d/%m/%Y %I:%M %p",
)
DEFAULT_NULL_VALUES = ("NULL", "NONE", "N/A", "NA", "NIL", "-", "--")

TRUE_WORDS = frozenset({"1", "y", "yes", "true", "t", "on", "active", "enabled"})
FALSE_WORDS = frozenset({"0", "n", "no", "false", "f", "off", "inactive", "disabled"})

# Excel stores dates as days since 1899-12-30 (the 1900 leap-year bug is baked in).
EXCEL_EPOCH = datetime.date(1899, 12, 30)

CURRENCY_NOISE = re.compile(r"(?i)(₹|rs\.?|inr|/-)")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PINCODE_RE = re.compile(r"^[1-9]\d{5}$")


class TransformError(ValueError):
	"""A cell could not be converted. The message is shown in the migration report."""


def is_empty(value) -> bool:
	return value is None or (isinstance(value, str) and not value.strip())


def to_text(value) -> str:
	"""Render a cell as text the way a person would read it in the spreadsheet."""
	if value is None:
		return ""
	if isinstance(value, bool):
		return "1" if value else "0"
	if isinstance(value, float) and value.is_integer():
		return str(int(value))
	if isinstance(value, datetime.datetime):
		return value.strftime("%Y-%m-%d %H:%M:%S")
	if isinstance(value, datetime.date):
		return value.strftime("%Y-%m-%d")
	if isinstance(value, Decimal):
		return format(value, "f")
	return str(value)


def clean_null(value, null_values: Iterable[str] = DEFAULT_NULL_VALUES):
	"""Treat placeholder text such as "NULL" or "-" as an empty cell."""
	if is_empty(value):
		return None
	if isinstance(value, str):
		stripped = value.strip()
		if stripped.upper() in {v.upper() for v in null_values}:
			return None
	return value


# ------------------------------------------------------------------------- text


def t_strip(value, params, context):
	return to_text(value).strip()


def t_collapse_spaces(value, params, context):
	return " ".join(to_text(value).split())


def t_upper(value, params, context):
	return to_text(value).strip().upper()


def t_lower(value, params, context):
	return to_text(value).strip().lower()


def t_title(value, params, context):
	"""Title-case names without mangling initials or Mc/O' prefixes too badly.

	"ARUN KUMAR" -> "Arun Kumar", "k.arun" -> "K.Arun", "SELVI-RANI" -> "Selvi-Rani".
	Words that are already mixed case are kept as typed ("McDonald", "DSouza").
	"""
	text = " ".join(to_text(value).split())

	def fix(word: str) -> str:
		if not word:
			return word
		if word != word.upper() and word != word.lower():
			return word
		return word[0].upper() + word[1:].lower()

	return re.sub(r"[^\s.\-']+", lambda m: fix(m.group(0)), text)


def t_replace(value, params, context):
	old = params.get("old")
	if old is None:
		raise TransformError("The replace transform needs an 'old' value.")
	return to_text(value).replace(str(old), str(params.get("new", "")))


def t_prefix(value, params, context):
	return f"{params.get('value', '')}{to_text(value).strip()}"


def t_suffix(value, params, context):
	return f"{to_text(value).strip()}{params.get('value', '')}"


def t_digits(value, params, context):
	return re.sub(r"\D", "", to_text(value))


def t_split_name(value, params, context):
	"""First word, or everything after it: "Arun Kumar R" -> "Arun" / "Kumar R"."""
	words = to_text(value).split()
	part = params.get("part", "first")
	if part not in ("first", "last"):
		raise TransformError("split_name part must be 'first' or 'last'.")
	if not words:
		return ""
	return words[0] if part == "first" else " ".join(words[1:])


def t_truncate(value, params, context):
	length = int(params.get("length", 140))
	return to_text(value).strip()[:length]


# ----------------------------------------------------------------- dates, numbers


def _formats(params, context, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
	formats = params.get("formats") or (context or {}).get(key) or default
	if isinstance(formats, str):
		formats = (formats,)
	return tuple(formats)


def _excel_serial_to_date(value) -> datetime.date:
	try:
		days = float(value)
	except (TypeError, ValueError) as e:
		raise TransformError(f"'{value}' is not an Excel date number.") from e
	if not 1 <= days <= 2958465:
		raise TransformError(f"'{value}' is not an Excel date number.")
	return EXCEL_EPOCH + datetime.timedelta(days=int(days))


def parse_date(value, formats: Iterable[str], excel_serial: bool = False) -> datetime.date:
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	if isinstance(value, int | float) and not isinstance(value, bool):
		if excel_serial:
			return _excel_serial_to_date(value)
		value = to_text(value)
	text = " ".join(to_text(value).split())
	if excel_serial and re.fullmatch(r"\d{4,6}(\.0+)?", text):
		return _excel_serial_to_date(text)
	for fmt in formats:
		try:
			return datetime.datetime.strptime(text, fmt).date()
		except ValueError:
			continue
	raise TransformError(f"'{text}' is not a date in any expected format ({', '.join(formats)}).")


def parse_datetime(value, formats: Iterable[str], date_formats: Iterable[str]) -> datetime.datetime:
	if isinstance(value, datetime.datetime):
		return value.replace(microsecond=0, tzinfo=None)
	if isinstance(value, datetime.date):
		return datetime.datetime.combine(value, datetime.time())
	text = " ".join(to_text(value).split())
	for fmt in formats:
		try:
			return datetime.datetime.strptime(text, fmt)
		except ValueError:
			continue
	# A date without a time is midnight; legacy systems often drop the time part.
	for fmt in date_formats:
		try:
			return datetime.datetime.strptime(text, fmt)
		except ValueError:
			continue
	raise TransformError(f"'{text}' is not a date and time in any expected format.")


def t_date(value, params, context):
	formats = _formats(params, context, "date_formats", DEFAULT_DATE_FORMATS)
	parsed = parse_date(value, formats, excel_serial=bool(params.get("excel_serial")))
	if not 1900 <= parsed.year <= 2200:
		raise TransformError(f"Year {parsed.year} in '{to_text(value)}' does not look right.")
	return parsed.isoformat()


def t_datetime(value, params, context):
	formats = _formats(params, context, "datetime_formats", DEFAULT_DATETIME_FORMATS)
	date_formats = _formats({}, context, "date_formats", DEFAULT_DATE_FORMATS)
	parsed = parse_datetime(value, formats, date_formats)
	if not 1900 <= parsed.year <= 2200:
		raise TransformError(f"Year {parsed.year} in '{to_text(value)}' does not look right.")
	return parsed.strftime("%Y-%m-%d %H:%M:%S")


def parse_decimal(value) -> Decimal:
	"""Parse Indian-style amounts: "12,500", "Rs. 12,500/-", "(450.00)", "₹ 1,00,000.5"."""
	if isinstance(value, Decimal):
		return value
	if isinstance(value, int | float) and not isinstance(value, bool):
		return Decimal(str(value))
	text = CURRENCY_NOISE.sub("", to_text(value)).replace(",", "").replace(" ", "").strip()
	negative = False
	if text.startswith("(") and text.endswith(")"):
		negative, text = True, text[1:-1]
	if text.endswith("-") and len(text) > 1:
		negative, text = True, text[:-1]
	if text.upper().endswith("CR"):
		text = text[:-2]
	elif text.upper().endswith("DR"):
		negative, text = True, text[:-2]
	try:
		number = Decimal(text)
	except InvalidOperation as e:
		raise TransformError(f"'{to_text(value)}' is not an amount.") from e
	if not number.is_finite():
		raise TransformError(f"'{to_text(value)}' is not an amount.")
	return -number if negative else number


def t_decimal(value, params, context):
	places = int(params.get("places", 2))
	quantum = Decimal(1).scaleb(-places)
	number = parse_decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)
	if params.get("non_negative") and number < 0:
		raise TransformError(f"'{to_text(value)}' must not be negative.")
	return float(number)


def t_integer(value, params, context):
	number = parse_decimal(value)
	if number != number.to_integral_value():
		raise TransformError(f"'{to_text(value)}' is not a whole number.")
	return int(number)


def t_boolean(value, params, context):
	if isinstance(value, bool):
		return int(value)
	text = to_text(value).strip().lower()
	if text in TRUE_WORDS or text in {w.lower() for w in params.get("true", ())}:
		return 1
	if text in FALSE_WORDS or text in {w.lower() for w in params.get("false", ())}:
		return 0
	raise TransformError(f"'{to_text(value)}' is not a yes/no value.")


# --------------------------------------------------------------- contact details


def t_mobile(value, params, context):
	"""10-digit Indian mobile through the same normaliser the application uses."""
	import frappe

	from hostel360.utils.validation import normalize_mobile

	text = to_text(value).strip()
	message_count = len(getattr(frappe.local, "message_log", None) or [])
	try:
		return normalize_mobile(text)
	except frappe.ValidationError as e:
		# normalize_mobile reports through frappe.throw; keep the message log of a long
		# run from filling up with one entry per bad number.
		if getattr(frappe.local, "message_log", None) is not None:
			del frappe.local.message_log[message_count:]
		raise TransformError(f"'{text}' is not a valid 10-digit Indian mobile number.") from e


def t_email(value, params, context):
	text = to_text(value).strip().lower()
	if not EMAIL_RE.match(text):
		raise TransformError(f"'{text}' is not an e-mail address.")
	return text


def t_pincode(value, params, context):
	text = re.sub(r"\s", "", to_text(value))
	if not PINCODE_RE.match(text):
		raise TransformError(f"'{to_text(value)}' is not a 6-digit PIN code.")
	return text


TRANSFORMS: dict[str, Callable] = {
	"strip": t_strip,
	"collapse_spaces": t_collapse_spaces,
	"upper": t_upper,
	"lower": t_lower,
	"title": t_title,
	"replace": t_replace,
	"prefix": t_prefix,
	"suffix": t_suffix,
	"digits": t_digits,
	"split_name": t_split_name,
	"truncate": t_truncate,
	"date": t_date,
	"datetime": t_datetime,
	"decimal": t_decimal,
	"integer": t_integer,
	"boolean": t_boolean,
	"mobile": t_mobile,
	"email": t_email,
	"pincode": t_pincode,
}


def normalise_transform(spec) -> tuple[str, dict]:
	"""``"date"`` or ``{"name": "date", "formats": [...]}`` -> ("date", {...})."""
	if isinstance(spec, str):
		name, params = spec, {}
	elif isinstance(spec, dict) and "name" in spec:
		params = {k: v for k, v in spec.items() if k != "name"}
		name = spec["name"]
	elif isinstance(spec, dict) and len(spec) == 1:
		name, params = next(iter(spec.items()))
		params = params if isinstance(params, dict) else {"value": params}
	else:
		raise ValueError(f"Transform {spec!r} must be a name or an object with a 'name'.")
	if name not in TRANSFORMS:
		raise ValueError(f"Unknown transform '{name}'. Known transforms: {', '.join(sorted(TRANSFORMS))}.")
	return name, params


def apply_transforms(value, transforms: Iterable, context: dict | None = None):
	"""Run the transforms in order. Empty results short-circuit to None."""
	for spec in transforms or ():
		if is_empty(value):
			return None
		name, params = normalise_transform(spec)
		value = TRANSFORMS[name](value, params, context or {})
	return None if is_empty(value) else value


def apply_value_map(value, value_map: dict | None, strict: bool = False):
	"""Translate legacy codes ("BOYS" -> "Male"). Keys match case-insensitively."""
	if not value_map or is_empty(value):
		return value
	text = to_text(value).strip()
	if text in value_map:
		return value_map[text]
	folded = {str(k).strip().lower(): v for k, v in value_map.items()}
	if text.lower() in folded:
		return folded[text.lower()]
	if strict:
		allowed = ", ".join(str(k) for k in value_map)
		raise TransformError(f"'{text}' is not one of the mapped values ({allowed}).")
	return value
