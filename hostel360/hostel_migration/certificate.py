"""Deletion certificates for the exit of an institution (tender TR 22).

A certificate is a JSON document ``{"certificate": {...}, "signature": {...}}``. The
signature is an HMAC-SHA256 over the canonical JSON of ``certificate`` (keys sorted, no
whitespace, UTF-8), keyed with a key derived from the site's ``encryption_key``. The same
content is also written as Markdown and, when the PDF renderer is available, as PDF for
the institution's file; the JSON is the authoritative copy.

The signing key is derived (HMAC of a fixed label) rather than used directly, so the
encryption key that protects stored passwords is never itself used to sign documents.
Verifying a certificate after the site is gone needs that encryption key, which HyFib keeps
in its password vault with the site's other recovery secrets.
"""

import hashlib
import hmac
import json
import os

FORMAT = "hostel360-deletion-certificate"
FORMAT_VERSION = 1
ALGORITHM = "HMAC-SHA256"
SIGNING_LABEL = b"hostel360/deletion-certificate/v1"


class CertificateError(ValueError):
	pass


def signing_key(encryption_key: str | None = None) -> bytes:
	if encryption_key is None:
		from frappe.utils.password import get_encryption_key

		encryption_key = get_encryption_key()
	encryption_key = (encryption_key or "").strip()
	if not encryption_key:
		raise CertificateError("No encryption key is available to sign or verify the certificate.")
	return hmac.new(encryption_key.encode("utf-8"), SIGNING_LABEL, hashlib.sha256).digest()


def key_fingerprint(key: bytes) -> str:
	"""A short public identifier of the signing key (it reveals nothing about the key)."""
	return hashlib.sha256(b"fingerprint:" + key).hexdigest()[:16]


def canonical_json(body: dict) -> bytes:
	return json.dumps(
		body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
	).encode("utf-8")


def sign(body: dict, key: bytes) -> dict:
	# A round trip through JSON first, so what is signed is exactly what a reader of the
	# file will see (tuples become lists, and so on).
	body = json.loads(canonical_json(body))
	return {
		"certificate": body,
		"signature": {
			"algorithm": ALGORITHM,
			"key_fingerprint": key_fingerprint(key),
			"value": hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest(),
		},
	}


def verify(document: dict, key: bytes) -> tuple[bool, str]:
	if not isinstance(document, dict) or not isinstance(document.get("certificate"), dict):
		return False, "This is not a Hostel360 deletion certificate."
	body, signature = document["certificate"], document.get("signature") or {}
	if body.get("format") != FORMAT:
		return False, "This is not a Hostel360 deletion certificate."
	if signature.get("algorithm") != ALGORITHM or not signature.get("value"):
		return False, "The certificate carries no signature this tool understands."
	if signature.get("key_fingerprint") != key_fingerprint(key):
		return False, "The certificate was signed with a different key (another site, or the wrong key file)."
	expected = hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()
	if not hmac.compare_digest(expected, str(signature["value"])):
		return False, "The signature does not match: the certificate was altered after it was issued."
	return True, "The signature is valid."


def load(path: str) -> dict:
	try:
		with open(path, encoding="utf-8") as f:
			return json.load(f)
	except (OSError, ValueError) as e:
		raise CertificateError(f"{path} cannot be read: {e}") from e


# ----------------------------------------------------------------------- rendering


def _count(value) -> str:
	return f"{int(value or 0):,}"


def _size(num_bytes) -> str:
	size = float(num_bytes or 0)
	if size < 1024:
		return f"{int(size):,} bytes"
	for unit in ("KiB", "MiB", "GiB"):
		size /= 1024
		if size < 1024 or unit == "GiB":
			break
	return f"{size:,.1f} {unit}"


def _cell(value) -> str:
	return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(document: dict) -> str:
	c = document["certificate"]
	sig = document.get("signature") or {}
	export = c.get("export") or {}
	deleted = c.get("deleted") or {}
	verification = c.get("verification") or {}
	lines = [
		f"# Certificate of Data Deletion: {c['certificate_id']}",
		"",
		f"**{c.get('issued_by', '')}** certifies that the institution data held in HyFib Hostel360 for "
		f"**{c.get('institution') or c['site']}** on the site `{c['site']}` was permanently deleted from the "
		"application, as listed below, after the institution confirmed in writing that it had received its exit export "
		"(tender clause TR 22).",
		"",
		"| | |",
		"|---|---|",
		f"| Institution's written confirmation | {_cell(c['acknowledgement_reference'])} |",
		f"| Deletion started | {_cell(c['started_at'])} |",
		f"| Deletion finished | {_cell(c['finished_at'])} |",
		f"| Time zone | {_cell(c.get('time_zone'))} |",
		f"| Carried out by | {_cell((c.get('operator') or {}).get('system_user'))} on {_cell((c.get('operator') or {}).get('host'))} |",
		f"| Result | {'All selected data deleted; nothing found remaining' if verification.get('clean') else 'Completed with items that need attention (see Verification)'} |",
		"",
		"## Exit export handed over",
		"",
	]
	if export:
		lines += [
			"| | |",
			"|---|---|",
			f"| Export | {_cell(export.get('export_name'))} |",
			f"| Made on | {_cell(export.get('exported_on'))} ({_cell(export.get('age_days'))} days before deletion) |",
			f"| Verified on | {_cell(export.get('verified_on'))} by {_cell(export.get('verified_by'))} |",
			f"| Manifest SHA-256 | `{_cell(export.get('manifest_sha256'))}` |",
			f"| Archive SHA-256 | `{_cell(export.get('archive_sha256') or '-')}` |",
			f"| Files in export | {_count(export.get('entry_count'))} ({_size(export.get('total_bytes'))}) |",
			"",
		]
	else:
		lines += ["No verified exit export was on record for this site.", ""]
	if c.get("forced_without_export"):
		lines += [
			f"> The deletion was forced without a qualifying export: {_cell(c.get('export_problem'))}",
			"",
		]

	for warning in c.get("warnings") or []:
		lines += [f"> Note: {_cell(warning)}", ""]

	lines += ["## Deleted", "", "### Hostel360 records", "", "| DocType | Kind | Rows |", "|---|---|---:|"]
	for row in deleted.get("hostel360_doctypes") or []:
		lines.append(f"| {_cell(row['doctype'])} | {_cell(row['kind'])} | {_count(row['rows'])} |")
	lines += ["", "### Related Frappe records", "", "| Record type | Rows |", "|---|---:|"]
	for row in deleted.get("frappe_records") or []:
		lines.append(f"| {_cell(row['doctype'])} | {_count(row['rows'])} |")
	lines += ["", "### Internal tables", "", "| Table | Rows |", "|---|---:|"]
	for row in deleted.get("internal_tables") or []:
		lines.append(f"| {_cell(row['table'])} | {_count(row['rows'])} |")

	users = deleted.get("users") or {}
	files = deleted.get("files") or {}
	lines += [
		"",
		"### Accounts and files",
		"",
		"| | Count | Size |",
		"|---|---:|---:|",
		f"| User accounts of students, parents and institution staff | {_count(users.get('count'))} | |",
		f"| Uploaded files (application file store) | {_count(files.get('deleted'))} | {_size(files.get('bytes'))} |",
	]
	for key, label in (
		("local_backups", "Backups kept on the application server"),
		("migration_sources", "Legacy data extracts used for migration"),
	):
		item = deleted.get(key) or {}
		lines.append(f"| {label} | {_count(item.get('files'))} | {_size(item.get('bytes'))} |")
	for copy in deleted.get("export_copies") or []:
		lines.append(
			f"| Server copy of the exit export: `{_cell(copy.get('path'))}` | {_cell(copy.get('result'))} | |"
		)
	totals = c.get("totals") or {}
	lines += [
		"",
		f"**Totals:** {_count(totals.get('records'))} records, {_count(totals.get('files'))} files "
		f"({_size(totals.get('bytes'))}).",
		"",
	]
	changed = c.get("changed_since_export") or []
	if changed:
		lines += [
			"Records added or changed after the export was made (deleted, but not in the export): "
			+ ", ".join(f"{_cell(row['doctype'])} {_count(row['rows'])}" for row in changed)
			+ ".",
			"",
		]
	for run in c.get("interrupted_runs") or []:
		lines += [
			f"An earlier run, {_cell(run.get('certificate_id'))} (started {_cell(run.get('started_at'))}), "
			f"stopped before issuing its certificate; it had selected {_count(run.get('planned_records'))} "
			f"records, {_count(run.get('planned_users'))} accounts and {_count(run.get('planned_files'))} files, "
			"which this run completed.",
			"",
		]

	lines += ["## Verification", ""]
	residual = verification.get("residual") or []
	if not residual:
		lines.append(
			"After deletion every table and folder above was counted again and nothing selected for deletion remained."
		)
	else:
		lines += ["Items still present after deletion:", "", "| Item | Remaining |", "|---|---:|"]
		for row in residual:
			lines.append(f"| {_cell(row['item'])} | {_cell(row['remaining'])} |")
	lines.append("")

	retained = c.get("retained") or []
	if retained:
		lines += ["## Kept on purpose", "", "| Item | Why |", "|---|---|"]
		for row in retained:
			lines.append(f"| {_cell(row['item'])} | {_cell(row['reason'])} |")
		lines.append("")

	outstanding = c.get("outstanding") or []
	if outstanding:
		lines += [
			"## Still to be destroyed outside the application",
			"",
			"These stores are outside the application and are destroyed with the exit runbook; each is signed off "
			"separately.",
			"",
		]
		for number, step in enumerate(outstanding, 1):
			lines.append(f"{number}. {step}")
		lines.append("")

	lines += [
		"## Signature",
		"",
		f"Algorithm {_cell(sig.get('algorithm'))}, key fingerprint `{_cell(sig.get('key_fingerprint'))}`:",
		"",
		f"`{_cell(sig.get('value'))}`",
		"",
		"The JSON file of the same name is the authoritative copy. Check it with "
		"`bench h360-verify-deletion-certificate --path <file>.json` (on the site, or with `--key-file`).",
		"",
		"Authorised signatory for HyFib Solutions Private Limited: ______________________  Date: ____________",
		"",
	]
	return "\n".join(lines)


def render_pdf(markdown_text: str, title: str) -> bytes:
	"""PDF of the Markdown rendering (needs the site's PDF renderer)."""
	from frappe.utils import escape_html, md_to_html
	from frappe.utils.pdf import get_pdf

	body = md_to_html(markdown_text) or ""
	html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{escape_html(title)}</title>
<style>
body {{ font-family: "Noto Sans", Arial, sans-serif; font-size: 10.5pt; color: #111; }}
h1 {{ font-size: 16pt; border-bottom: 1px solid #444; padding-bottom: 4px; }}
h2 {{ font-size: 12.5pt; margin-top: 18px; }}
h3 {{ font-size: 11pt; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0; }}
th, td {{ border: 1px solid #bbb; padding: 3px 6px; text-align: left; vertical-align: top; }}
code {{ font-family: "DejaVu Sans Mono", monospace; font-size: 8.5pt; word-break: break-all; }}
blockquote {{ border-left: 3px solid #c60; margin: 6px 0; padding-left: 8px; }}
</style></head><body>{body}</body></html>"""
	return get_pdf(html, {"page-size": "A4", "margin-top": "15mm", "margin-bottom": "15mm"})


def write(document: dict, directory: str, pdf: bool = True) -> dict:
	"""Write ``<id>.json``, ``<id>.md`` and (if possible) ``<id>.pdf``; return their paths."""
	os.makedirs(directory, mode=0o700, exist_ok=True)
	stem = os.path.join(directory, document["certificate"]["certificate_id"])
	paths = {"json": stem + ".json", "markdown": stem + ".md", "pdf": None, "pdf_error": None}

	markdown_text = render_markdown(document)
	for path, content in (
		(paths["json"], json.dumps(document, indent=1, ensure_ascii=False) + "\n"),
		(paths["markdown"], markdown_text),
	):
		write_private(path, content.encode("utf-8"))
	if pdf:
		try:
			write_private(stem + ".pdf", render_pdf(markdown_text, document["certificate"]["certificate_id"]))
			paths["pdf"] = stem + ".pdf"
		except Exception as e:
			# The PDF is a convenience copy; the signed JSON and the Markdown already exist.
			paths["pdf_error"] = str(e)[:300] or e.__class__.__name__
	return paths


def write_private(path: str, content: bytes) -> None:
	"""Write a file readable by its owner only, atomically."""
	tmp = path + ".part"
	fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	try:
		with os.fdopen(fd, "wb") as f:
			f.write(content)
			f.flush()
			os.fsync(f.fileno())
		os.replace(tmp, path)
	finally:
		if os.path.exists(tmp):
			os.remove(tmp)
