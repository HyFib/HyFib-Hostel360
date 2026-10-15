"""Template rendering for messages.

Templates are edited by administrators, so they are rendered in Frappe's sandboxed Jinja
environment with the restricted globals, and attribute access to dunder names is refused
outright. Rendering never raises Frappe message popups: errors surface as
``TemplateRenderError`` so the dispatcher can log them against the Message Log row.
"""

import datetime
import html
import re
from contextlib import nullcontext
from functools import partial
from urllib.parse import urlparse

import frappe
from frappe.utils import cstr, flt, formatdate, get_datetime, get_url

from hostel360.hostel_communication.events import DEFAULT_LANGUAGE, EVENT_CONTEXT, normalize_language
from hostel360.hostel_communication.labels import label

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")

# Characters of the GSM 03.38 basic set (plus the extension table); anything else forces
# UCS-2 encoding, which cuts an SMS segment from 160 to 70 characters.
GSM_BASIC = set(
	"@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
	"¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_EXTENDED = set("^{}\\[~]|€\f")

COMMON_VARIABLES = (
	"recipient_name",
	"recipient_kind",
	"institution_name",
	"institution_short_name",
	"portal_url",
	"portal_domain",
	"support_phone",
	"support_email",
	"site_url",
)

DEFAULT_SUPPORT_PHONE = "+91 96060 60596"
DEFAULT_SUPPORT_EMAIL = "support@hyfib.com"
DEFAULT_SHORT_NAME = "ACGCET"
DATETIME_FORMAT = "dd-MM-yyyy hh:mm a"


class TemplateRenderError(Exception):
	"""A message template could not be compiled or rendered."""


# ---------------------------------------------------------------- template helpers


def _is_blank(value) -> bool:
	from jinja2 import Undefined

	return value is None or value == "" or isinstance(value, Undefined)


def rupees(amount) -> str:
	"""ASCII rupee amount with Indian grouping: 45250 -> "Rs.45,250", 118.5 -> "Rs.118.50"."""
	if _is_blank(amount):
		return ""
	from hostel360.utils.jinja import format_inr

	value = flt(amount, 2)
	precision = 0 if value == int(value) else 2
	return format_inr(value, precision).replace("₹", "Rs.")


def inr(amount) -> str:
	if _is_blank(amount):
		return ""
	from hostel360.utils.jinja import format_inr

	return format_inr(amount)


def _is_machine_date(value) -> bool:
	"""Dates we can safely reformat: date objects and ISO strings.

	Callers sometimes pass dates they have already formatted for display ("05-07-2026");
	re-parsing those could swap day and month, so they are shown exactly as given.
	"""
	if isinstance(value, datetime.date):
		return True
	return isinstance(value, str) and bool(ISO_DATE_RE.match(value.strip()))


def fdate(value) -> str:
	if _is_blank(value):
		return ""
	if not _is_machine_date(value):
		return cstr(value)
	try:
		return formatdate(value)
	except Exception:
		return cstr(value)


def fdatetime(value, language: str | None = None) -> str:
	"""Date and time in the template's language ("04-07-2026 04:00 PM").

	The locale comes from the template, not from ``frappe.local.lang``: a delivery job takes
	on the language of whoever queued it, and a warden's English message must not change
	because a Tamil-speaking student applied for leave.
	"""
	if _is_blank(value):
		return ""
	if not _is_machine_date(value):
		return cstr(value)
	try:
		import babel.dates

		return babel.dates.format_datetime(
			get_datetime(value), DATETIME_FORMAT, locale=normalize_language(language)
		)
	except Exception:
		return cstr(value)


def short(value, length: int = 30) -> str:
	"""Truncate text to ``length`` characters (DLT variables are limited to 30)."""
	if _is_blank(value):
		return ""
	text = re.sub(r"\s+", " ", cstr(value)).strip()
	length = max(int(length or 0), 4)
	return text if len(text) <= length else text[: length - 3].rstrip() + "..."


TEMPLATE_HELPERS = {
	"rupees": rupees,
	"format_inr": inr,
	"fdate": fdate,
	"fdatetime": fdatetime,
	"short": short,
	"label": label,
}


def helpers_for(language: str | None = None) -> dict:
	"""Template helpers, with the language-dependent ones bound to ``language``."""
	language = normalize_language(language)
	if language == DEFAULT_LANGUAGE:
		return dict(TEMPLATE_HELPERS)
	return {
		**TEMPLATE_HELPERS,
		"fdatetime": partial(fdatetime, language=language),
		"label": partial(label, language=language),
	}


# ---------------------------------------------------------------- context


def get_base_context() -> dict:
	"""Institution and portal details available to every template."""
	cached = getattr(frappe.local, "hostel360_message_base_context", None)
	if cached is not None:
		return dict(cached)

	settings = {}
	if frappe.db.exists("DocType", "Hostel Settings"):
		try:
			doc = frappe.get_cached_doc("Hostel Settings")
			settings = {
				"institution_name": doc.get("institution_name"),
				"institution_short_name": doc.get("institution_short_name"),
				"portal_url": doc.get("portal_url"),
				"support_phone": doc.get("support_phone"),
				"support_email": doc.get("support_email"),
			}
		except Exception:
			settings = {}

	site_url = get_url()
	portal_url = (settings.get("portal_url") or "").rstrip("/") or f"{site_url.rstrip('/')}/hostel"
	context = {
		"institution_name": settings.get("institution_name") or "",
		"institution_short_name": settings.get("institution_short_name") or DEFAULT_SHORT_NAME,
		"portal_url": portal_url,
		"portal_domain": urlparse(portal_url).hostname or "",
		"support_phone": settings.get("support_phone") or DEFAULT_SUPPORT_PHONE,
		"support_email": settings.get("support_email") or DEFAULT_SUPPORT_EMAIL,
		"site_url": site_url,
	}
	frappe.local.hostel360_message_base_context = context
	return dict(context)


def clear_base_context_cache() -> None:
	if hasattr(frappe.local, "hostel360_message_base_context"):
		del frappe.local.hostel360_message_base_context


def to_plain(value, _depth: int = 0):
	"""Reduce a context value to plain data (documents become dicts) so it can be queued."""
	from frappe.model.document import Document

	if _depth > 4:
		return cstr(value)
	if isinstance(value, Document):
		return to_plain(value.as_dict(no_default_fields=True), _depth + 1)
	if isinstance(value, dict):
		return {cstr(k): to_plain(v, _depth + 1) for k, v in value.items() if not callable(v)}
	if isinstance(value, list | tuple | set):
		return [to_plain(v, _depth + 1) for v in value]
	if value is None or isinstance(value, str | int | float | bool):
		return value
	# dates, datetimes, Decimals and the like keep their text form
	return cstr(value)


def escape_values(value):
	"""HTML-escape every string in a context so student-entered text cannot inject markup."""
	if isinstance(value, str):
		return html.escape(value, quote=True)
	if isinstance(value, dict):
		return {k: escape_values(v) for k, v in value.items()}
	if isinstance(value, list):
		return [escape_values(v) for v in value]
	return value


def build_context(
	context: dict | None,
	recipient: dict | None = None,
	*,
	html_safe: bool = False,
	language: str | None = None,
) -> dict:
	"""Template context: institution details, the caller's values, recipient and helpers.

	``language`` is the template's language; it decides how dates and fixed values read.
	"""
	recipient = recipient or {}
	merged = get_base_context()
	for key, value in to_plain(dict(context or {})).items():
		# A caller passing an empty institution name must not blank out the configured one.
		if key in merged and value in (None, ""):
			continue
		merged[key] = value
	if not merged.get("recipient_name"):
		merged["recipient_name"] = recipient.get("name") or ""
	if not merged.get("recipient_kind"):
		merged["recipient_kind"] = recipient.get("kind") or ""
	if html_safe:
		merged = escape_values(merged)
	merged.update(helpers_for(language))
	return merged


def available_variables(event: str) -> list[str]:
	return [*EVENT_CONTEXT.get(event, ()), *COMMON_VARIABLES]


# ---------------------------------------------------------------- rendering


def _environment():
	from frappe.utils.jinja import get_jenv

	# The restricted globals read locale settings, which fails when no language has been
	# set on this thread (scheduler jobs started outside a request).
	if not getattr(frappe.local, "lang", None):
		frappe.local.lang = frappe.db.get_default("lang") or "en"
	return get_jenv(restrict_globals=True)


def _guard(text: str) -> None:
	if ".__" in text or "__class__" in text or "__globals__" in text:
		raise TemplateRenderError("Templates may not access internal attributes.")


def compile_check(text: str | None) -> None:
	"""Raise TemplateRenderError when the template does not compile."""
	from jinja2 import TemplateSyntaxError

	if not text:
		return
	_guard(text)
	try:
		_environment().from_string(text)
	except TemplateSyntaxError as e:
		raise TemplateRenderError(f"Line {e.lineno}: {e.message}") from e


def render(text: str | None, context: dict) -> str:
	if not text:
		return ""
	_guard(text)
	try:
		from frappe.utils.jinja import safe_render_flags
	except ImportError:
		safe_render_flags = nullcontext

	try:
		compiled = _environment().from_string(text)
		with safe_render_flags():
			return compiled.render(context)
	except TemplateRenderError:
		raise
	except Exception as e:
		raise TemplateRenderError(f"{type(e).__name__}: {e}") from e


def render_parameters(lines: str | None, context: dict) -> list[str]:
	"""Render one Jinja expression per line into provider template parameters."""
	params = []
	for line in (lines or "").splitlines():
		expression = line.strip()
		if not expression:
			continue
		if "{{" not in expression and "{%" not in expression:
			expression = "{{ " + expression + " }}"
		params.append(render(expression, context).strip())
	return params


def plain_text(value: str) -> str:
	"""Collapse whitespace (WhatsApp parameters may not contain new lines or tabs)."""
	return re.sub(r"\s+", " ", cstr(value)).strip()


def is_unicode(text: str | None) -> bool:
	"""True when the text needs UCS-2 (Unicode) SMS encoding, as Tamil text always does."""
	return any(ch not in GSM_BASIC and ch not in GSM_EXTENDED for ch in text or "")


def sms_stats(text: str) -> dict:
	"""Length, encoding and segment count of an SMS body."""
	text = text or ""
	unicode = is_unicode(text)
	if unicode:
		length = len(text)
		single, multi = 70, 67
	else:
		length = sum(2 if ch in GSM_EXTENDED else 1 for ch in text)
		single, multi = 160, 153
	segments = 1 if length <= single else -(-length // multi)
	return {"length": length, "unicode": unicode, "segments": segments if text else 0}
