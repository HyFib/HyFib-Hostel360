"""Upload security for File records.

The policy:

* extension allow-list: PDF and images for students and parents, plus spreadsheets for
  staff;
* the content really is what the extension claims (magic bytes; spreadsheets are
  opened as ZIP or OLE containers; CSV must be plain text);
* size: 5 MB for portal users, 10 MB for staff;
* portal uploads are always private;
* file names are cleaned of path parts, control characters, markup and extra dots;
* students, parents and visitors upload only through the Hostel360 portal endpoint
  (``hostel360.api.portal.upload_attachment``), which applies its quota and rate limit
  and keeps every file in the folder the daily clean-up covers.

``HostelFile`` (``override_doctype_class`` for File) applies the policy before Frappe
writes anything to disk, so a rejected upload never touches the file system.
``validate_upload`` (File ``before_insert``) is the same policy for sites where the
override is not active: it runs after Frappe has written the upload and removes the
bytes again when the upload is refused. With the override active it does nothing.

``block_portal_generic_upload`` (``before_request``) stops portal users and visitors at
Frappe's generic upload and insert endpoints before the request body is read. That
matters for ``upload_file``: a chunked call appends to a temporary file and returns
before any File record exists, so no File hook would ever see it.

``enqueue_malware_scan`` (File ``after_insert``) queues a ClamAV scan. Infected files are
deleted with every record that points to the same content and HyFib operations get a
critical alert. If the scanner cannot be reached the upload stays (fail open), a
warning alert is raised, and the file is scanned once the scanner is back.

Only interactive uploads are policed: files that Frappe or Hostel360 write themselves in
background jobs (prepared reports, backups, receipts and certificates) are trusted.
"""

import io
import json
import os
import re
import unicodedata
import zipfile

import frappe
from frappe import _
from frappe.core.doctype.file.file import File
from frappe.utils import cint, cstr

from hostel360.security import clamav
from hostel360.security.audit import security_log

MB = 1024 * 1024
PORTAL_EXTENSIONS = frozenset({"pdf", "jpg", "jpeg", "png", "webp"})
STAFF_EXTENSIONS = PORTAL_EXTENSIONS | {"xlsx", "xls", "csv"}
PORTAL_MAX_BYTES = 5 * MB
STAFF_MAX_BYTES = 10 * MB
FILENAME_STEM_LIMIT = 100
TEXT_SAMPLE_BYTES = 64 * 1024

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"

DEFAULT_CLAMD_HOST = "clamav"
DEFAULT_CLAMD_PORT = 3310
SCAN_METHOD = "hostel360.security.files.scan_file"
UNSCANNED_SET = "hostel360:security:unscanned_files"
SCANNER_DOWN_KEY = "hostel360:security:scanner_down"
SCANNER_ALERT_KEY = "malware-scanner-unreachable"
SCANNER_ALERT_INTERVAL = 15 * 60
BACKLOG_BATCH = 25

# Characters that are unsafe or confusing in file names and URLs. Letters of any script
# (Tamil included) are kept.
UNSAFE_NAME_CHARS = re.compile(r"[<>:\"/\\|?*%#&;$`'{}\[\]=+!@^~,.]")

# Set on a File by Hostel360 code that inserts a portal user's upload after its own checks.
PORTAL_UPLOAD_FLAG = "hostel360_portal_upload"
# Hostel360 endpoints that create File records for portal users themselves (quota, rate
# limit and folder are theirs). A File inserted while serving one of them is accepted from
# a portal user without the flag.
PORTAL_UPLOAD_METHODS = frozenset({"hostel360.api.portal.upload_attachment"})

# Frappe's upload endpoint under every route that reaches it. The v1 router keeps only the
# part before the first slash of a method path, so ".../upload_file/anything" is the same
# call; v2 has a fixed route that skips override_whitelisted_methods.
GENERIC_UPLOAD_PATH = re.compile(r"^/api/(?:v[12]/)?method/(?:frappe\.handler\.)?upload_file(?:/.*)?$")
FILE_RESOURCE_PATH = re.compile(r"^/api/(?:(?:v1/)?resource|v2/document)/File/?$")
API_METHOD_PATH = re.compile(r"^/api/(?:v[12]/)?method/([^/]+)")
UPLOAD_COMMANDS = frozenset({"upload_file", "frappe.handler.upload_file"})
# Generic document endpoints that can insert a File from a JSON body.
GENERIC_INSERT_METHODS = frozenset(
	{
		"frappe.client.insert",
		"frappe.client.insert_many",
		"frappe.client.save",
		"frappe.desk.form.save.savedocs",
	}
)


class UploadRejected(frappe.ValidationError):
	pass


class PortalUploadBlocked(frappe.PermissionError):
	"""A student, parent or visitor tried to store a file outside the portal's upload endpoint."""


# ---------------------------------------------------------------- File controller


class HostelFile(File):
	"""File with the Hostel360 upload policy applied before Frappe stores the content."""

	def before_insert(self):
		enforce_upload_policy(self)
		super().before_insert()


def enforce_upload_policy(doc) -> None:
	"""Check a new File while its content is still in memory.

	Rejected uploads raise before anything is written. Accepted ones leave here with a
	clean name, decoded content and (for portal users) ``is_private`` set, so Frappe
	stores them once, in the right place, under the right name.
	"""
	if not _should_check(doc):
		return
	# validate_upload (the doc_events hook) runs after this and must not check again.
	doc.flags.hostel360_upload_checked = True

	portal = is_portal_uploader()
	if portal:
		_require_portal_channel(doc)
	if _is_remote(doc):
		if portal:
			_reject(doc, _("Please upload the file from your device instead of a link."), "remote_link")
		return

	content = _pending_content(doc)
	if content is None:
		# A File that points at bytes already stored (attaching an existing file).
		content = _content_bytes(doc)
	else:
		doc.content = content
		doc.decode = False

	doc.file_name = _checked_name(doc, content, portal)
	if portal:
		doc.is_private = 1


def mark_portal_upload(doc) -> None:
	"""Accept this File from a portal user (the caller has applied the portal's upload rules)."""
	doc.flags[PORTAL_UPLOAD_FLAG] = True


# ---------------------------------------------------------------- before_insert hook


def validate_upload(doc, method=None) -> None:
	"""File before_insert hook: the policy for bytes Frappe has already written."""
	if doc.flags.hostel360_upload_checked or not _should_check(doc):
		return

	portal = is_portal_uploader()
	if portal:
		_require_portal_channel(doc)
	if _is_remote(doc):
		if portal:
			_reject(doc, _("Please upload the file from your device instead of a link."), "remote_link")
		return

	content = _content_bytes(doc)
	name = _checked_name(doc, content, portal)
	make_private = portal and not cint(doc.is_private)
	if make_private or name != doc.file_name:
		_rewrite(doc, content, name, make_private)


def _checked_name(doc, content: bytes, portal: bool) -> str:
	"""The clean file name, after the type, size and content checks (raises when refused)."""
	name = sanitize_file_name(doc.file_name or os.path.basename(cstr(doc.file_url)))
	ext = file_extension(name)
	allowed = PORTAL_EXTENSIONS if portal else STAFF_EXTENSIONS

	if ext not in allowed:
		_reject(
			doc,
			_("Files of this type cannot be uploaded. Please upload a {0} file.").format(
				_("PDF, JPG, PNG or WEBP") if portal else _("PDF, image, Excel or CSV")
			),
			"type_not_allowed",
		)

	limit = PORTAL_MAX_BYTES if portal else STAFF_MAX_BYTES
	size = len(content)
	if not size:
		_reject(doc, _("The file is empty. Please choose the file again."), "empty")
	if size > limit:
		_reject(
			doc,
			_("This file is {0} MB. Please upload a file smaller than {1} MB.").format(
				round(size / MB, 1), limit // MB
			),
			"too_large",
		)

	if not content_matches(ext, content):
		_reject(
			doc,
			_(
				"This file does not look like a valid {0} file. It may be damaged or renamed. "
				"Please upload the original file."
			).format(ext.upper()),
			"content_mismatch",
		)
	if ext == "xlsx" and xlsx_has_macros(content):
		_reject(doc, _("Spreadsheets containing macros cannot be uploaded."), "macro")
	return name


def _should_check(doc) -> bool:
	if doc.is_folder or doc.flags.copy_from_existing_file:
		return False
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return False
	if doc.flags.hostel360_trusted_file:
		return False
	return bool(getattr(frappe.local, "request", None)) or bool(frappe.flags.hostel360_enforce_upload_policy)


def is_portal_uploader(user: str | None = None) -> bool:
	"""Students, parents and anonymous visitors get the strict policy; desk users the staff one."""
	user = user or frappe.session.user
	if user == "Administrator":
		return False
	if not user or user == "Guest":
		return True
	return frappe.get_cached_value("User", user, "user_type") != "System User"


def _require_portal_channel(doc) -> None:
	"""Portal users store files only through the portal's own upload endpoint.

	Any API call by a student, parent or visitor that would store a file - Frappe's
	upload_file, /api/resource/File, frappe.client.insert and the like - is refused unless
	it is one of ``PORTAL_UPLOAD_METHODS`` (or the File carries the portal flag). Requests
	outside /api are page views; they do not store what the visitor sends.
	"""
	if doc.flags.get(PORTAL_UPLOAD_FLAG):
		return
	call = current_api_call()
	if call is None or call in PORTAL_UPLOAD_METHODS:
		return
	_reject(
		doc,
		_("Please attach files from the Hostel360 portal."),
		"generic_upload",
		exc=PortalUploadBlocked,
	)


def current_api_call() -> str | None:
	"""What this request calls: a whitelisted method's dotted path, another /api path as it
	is, or None for requests that are not API calls (and outside requests)."""
	request = getattr(frappe.local, "request", None)
	if request is None:
		return None
	path = cstr(getattr(request, "path", ""))
	if path.startswith("/api/"):
		match = API_METHOD_PATH.match(path)
		return (match.group(1).strip() if match else "") or path
	# The deprecated RPC form: POST / with cmd=<method>.
	return cstr((frappe.form_dict or {}).get("cmd")).strip() or None


def _is_remote(doc) -> bool:
	return cstr(doc.file_url).lower().startswith(("http://", "https://"))


def _pending_content(doc) -> bytes | None:
	"""The bytes a new File is about to store, decoded the way Frappe would decode them."""
	content = doc.get("content")
	if not content:
		return None
	if doc.get("decode"):
		from frappe.core.doctype.file.utils import decode_file_content

		try:
			content = decode_file_content(content)
		except Exception:
			_reject(doc, _("The file could not be read. Please choose the file again."), "undecodable")
	if isinstance(content, str):
		content = content.encode("utf-8")
	return bytes(content)


def _content_bytes(doc) -> bytes:
	content = getattr(doc, "_content", None) or doc.get("content")
	if not content and doc.file_url:
		try:
			with open(doc.get_full_path(), "rb") as fh:
				content = fh.read()
		except OSError:
			content = b""
	if isinstance(content, str):
		content = content.encode("utf-8")
	return content or b""


def sanitize_file_name(name: str | None) -> str:
	"""Safe, readable file name: no path, no control characters, one dot, lower-case extension."""
	name = unicodedata.normalize("NFKC", cstr(name))
	name = name.replace("\\", "/").rsplit("/", 1)[-1]
	# Leading dots would hide the file on Unix and make ".htaccess" look like an extension.
	name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().lstrip(".")
	stem, dot, ext = name.rpartition(".")
	if not dot:
		stem, ext = name, ""
	stem = UNSAFE_NAME_CHARS.sub("_", stem)
	stem = re.sub(r"\s+", " ", stem)
	stem = re.sub(r"_{2,}", "_", stem).strip(" _-")
	stem = stem[:FILENAME_STEM_LIMIT].rstrip(" _-") or "file"
	ext = re.sub(r"[^A-Za-z0-9]", "", ext).lower()[:10]
	return f"{stem}.{ext}" if ext else stem


def file_extension(name: str) -> str:
	return name.rpartition(".")[2].lower() if "." in name else ""


def detect_kind(content: bytes) -> str | None:
	"""What the bytes are, regardless of the name they came with."""
	if content.startswith(PNG_MAGIC):
		return "png"
	if content.startswith(JPEG_MAGIC):
		return "jpeg"
	if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
		return "webp"
	if content.startswith(OLE_MAGIC):
		return "xls"
	if content.startswith(ZIP_MAGIC):
		return "xlsx" if is_xlsx(content) else "zip"
	if b"%PDF-" in content[:1024]:
		return "pdf"
	if is_plain_text(content):
		return "csv"
	return None


def content_matches(ext: str, content: bytes) -> bool:
	if ext == "pdf":
		# The PDF specification lets the header appear anywhere in the first 1024 bytes.
		return b"%PDF-" in content[:1024]
	if ext in ("jpg", "jpeg", "png", "webp"):
		# Phones sometimes save a PNG with a .jpg name; any real image is acceptable.
		return detect_kind(content) in ("jpeg", "png", "webp")
	if ext == "xlsx":
		return content.startswith(ZIP_MAGIC) and is_xlsx(content)
	if ext == "xls":
		return content.startswith(OLE_MAGIC)
	if ext == "csv":
		return is_plain_text(content)
	return False


def _zip_names(content: bytes) -> set[str] | None:
	try:
		with zipfile.ZipFile(io.BytesIO(content)) as archive:
			return set(archive.namelist())
	except (zipfile.BadZipFile, OSError, ValueError):
		return None


def is_xlsx(content: bytes) -> bool:
	names = _zip_names(content)
	return bool(names) and "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names)


def xlsx_has_macros(content: bytes) -> bool:
	names = _zip_names(content) or set()
	return any(n.lower().endswith("vbaproject.bin") for n in names)


def is_plain_text(content: bytes) -> bool:
	sample = content[:TEXT_SAMPLE_BYTES]
	if not sample or b"\x00" in sample:
		return False
	if sample.startswith(b"\xef\xbb\xbf"):
		sample = sample[3:]
	try:
		# A multi-byte character may be cut at the end of the sample.
		text = sample.decode("utf-8", errors="strict" if len(content) <= TEXT_SAMPLE_BYTES else "ignore")
	except UnicodeDecodeError:
		try:
			text = sample.decode("cp1252")
		except UnicodeDecodeError:
			return False
	return not any((ord(ch) < 32 and ch not in "\t\n\r\f") or ord(ch) == 127 for ch in text)


def _rewrite(doc, content: bytes, name: str, make_private: bool) -> None:
	"""Store the upload again under its clean name (and privately), removing the first copy."""
	from frappe.core.doctype.file.utils import delete_file

	old_url = doc.file_url
	doc.file_name = name
	if make_private:
		doc.is_private = 1
	doc.file_url = None
	doc.content_hash = None
	doc.save_file(content=content, decode=False)
	if old_url and old_url != doc.file_url and not frappe.db.exists("File", {"file_url": old_url}):
		delete_file(old_url)


def _reject(doc, message: str, reason: str = "rejected", exc=UploadRejected) -> None:
	from frappe.core.doctype.file.utils import delete_file

	security_log(
		"upload_rejected",
		ip=getattr(frappe.local, "request_ip", None),
		reason=reason,
		user_type="portal" if is_portal_uploader() else "staff",
		extension=file_extension(cstr(doc.file_name)) or None,
	)
	# Frappe has already written the bytes; do not leave them behind.
	if doc.flags.new_file and doc.file_url and not _is_remote(doc):
		if not frappe.db.exists("File", {"file_url": doc.file_url}):
			delete_file(doc.file_url)
		# File.on_rollback would otherwise write its "original content" - the rejected upload
		# itself - back to the same path when the request is rolled back.
		for flag in ("new_file", "original_content", "original_path"):
			doc.flags.pop(flag, None)
	frappe.throw(message, exc, title=_("Upload Not Allowed"))


# ---------------------------------------------------------------- before_request


def block_portal_generic_upload() -> None:
	"""before_request: keep students, parents and visitors off Frappe's generic upload paths.

	Runs before the upload body is read, so a chunked ``upload_file`` call is refused
	before Frappe appends to a temporary file. Requests that authenticate with an API key
	or token are not judged here (Frappe resolves that user later); every File they insert
	still meets the policy above.
	"""
	request = getattr(frappe.local, "request", None)
	if request is None or request.method == "OPTIONS":
		return
	# Every request passes through here; the path test is cheap, the user lookup is not.
	target = generic_upload_target(request)
	if not target or _authenticates_with_token(request) or not is_portal_uploader():
		return
	security_log(
		"generic_upload_blocked",
		ip=getattr(frappe.local, "request_ip", None),
		target=target,
		user_type="guest" if frappe.session.user in (None, "", "Guest") else "portal",
	)
	frappe.throw(
		_("Files can only be uploaded from the Hostel360 portal."),
		PortalUploadBlocked,
		title=_("Upload Not Allowed"),
	)


def generic_upload_target(request) -> str | None:
	"""Which generic upload or File insert endpoint the request is for, if any."""
	path = cstr(getattr(request, "path", ""))
	if GENERIC_UPLOAD_PATH.match(path):
		return "upload_file"
	if FILE_RESOURCE_PATH.match(path) and request.method in ("POST", "PUT"):
		return "file_resource"
	method = current_api_call()
	if not method:
		return None
	if method in UPLOAD_COMMANDS:
		return "upload_file"
	if method in GENERIC_INSERT_METHODS and _form_inserts_file():
		return method
	return None


def _form_inserts_file() -> bool:
	form = frappe.form_dict or {}
	for key in ("doc", "docs"):
		value = form.get(key)
		if isinstance(value, str | bytes):
			try:
				value = json.loads(value)
			except ValueError:
				continue
		values = value if isinstance(value, list) else [value]
		if any(isinstance(item, dict) and item.get("doctype") == "File" for item in values):
			return True
	return False


def _authenticates_with_token(request) -> bool:
	# Split exactly as frappe.auth.validate_auth does: only "<scheme> <credentials>" is
	# authenticated (or refused) there; any other header leaves a cookie or Guest request.
	header = cstr((getattr(request, "headers", None) or {}).get("Authorization"))
	return len(header.split(" ")) == 2


# ---------------------------------------------------------------- after_insert: malware scan


def scanner_config() -> tuple[str, int] | None:
	"""clamd address from site_config; None when scanning is switched off with clamd_host = ""."""
	if "clamd_host" in frappe.conf and not frappe.conf.get("clamd_host"):
		return None
	host = cstr(frappe.conf.get("clamd_host") or DEFAULT_CLAMD_HOST).strip()
	port = cint(frappe.conf.get("clamd_port")) or DEFAULT_CLAMD_PORT
	return host, port


def enqueue_malware_scan(doc, method=None) -> None:
	if doc.is_folder or doc.flags.copy_from_existing_file or not doc.file_url or _is_remote(doc):
		return
	if scanner_config() is None:
		return
	if frappe.in_test and not frappe.flags.hostel360_scan_in_tests:
		return
	frappe.enqueue(
		SCAN_METHOD,
		queue="short",
		job_id=f"hostel360-scan-{doc.name}",
		deduplicate=True,
		enqueue_after_commit=True,
		file_name=doc.name,
	)


def scan_file(file_name: str) -> str | None:
	"""Background job: scan one File. Returns "clean", "infected", "unscanned" or None."""
	config = scanner_config()
	if config is None or not frappe.db.exists("File", file_name):
		return None
	doc = frappe.get_doc("File", file_name)
	if doc.is_folder or _is_remote(doc):
		return None
	try:
		with open(doc.get_full_path(), "rb") as fh:
			data = fh.read()
	except OSError:
		return None

	host, port = config
	try:
		result = clamav.scan_bytes(data, host, port)
	except clamav.ScannerUnavailable as e:
		_scanner_unreachable(file_name, host, port, str(e))
		return "unscanned"

	_scanner_reachable()
	if result.clean:
		return "clean"
	quarantine(doc, result.signature)
	return "infected"


def quarantine(doc, signature: str | None) -> list[str]:
	"""Delete an infected file, every record sharing its content, and the bytes on disk."""
	from frappe.core.doctype.file.utils import delete_file

	details = {
		"file": doc.name,
		"file_name": doc.file_name,
		"signature": signature,
		"uploaded_by": doc.owner,
		"attached_to_doctype": doc.attached_to_doctype,
		"attached_to_name": doc.attached_to_name,
		"is_private": cint(doc.is_private),
		"size_bytes": cint(doc.file_size),
		"content_hash": doc.content_hash,
	}

	names = {doc.name}
	names.update(frappe.get_all("File", filters={"file_url": doc.file_url}, pluck="name"))
	if doc.content_hash:
		names.update(frappe.get_all("File", filters={"content_hash": doc.content_hash}, pluck="name"))

	removed = []
	for name in sorted(names):
		row = frappe.db.get_value(
			"File",
			name,
			[
				"name",
				"file_url",
				"thumbnail_url",
				"file_name",
				"attached_to_doctype",
				"attached_to_name",
				"attached_to_field",
			],
			as_dict=True,
		)
		if not row:
			continue
		for url in (row.file_url, row.thumbnail_url):
			if url and not url.lower().startswith(("http://", "https://")):
				try:
					delete_file(url)
				except Exception:
					frappe.log_error(
						title="Hostel360: could not delete infected file", message=frappe.get_traceback()
					)
		_detach(row, signature)
		# Bypass File.on_trash: an infected attachment must go even from a submitted document.
		frappe.db.delete("File", {"name": name})
		removed.append(name)

	security_log(
		"malware_detected",
		level="warning",
		signature=signature,
		file=doc.name,
		uploaded_by_hash=_hash(doc.owner),
		removed=len(removed),
	)
	_alert(
		key=f"malware-detected-{(doc.content_hash or doc.name)[:16]}",
		title=_("Malware detected in an uploaded file"),
		severity="Critical",
		description=_(
			"ClamAV found {0} in {1}, uploaded by {2}. The file and {3} record(s) pointing to it were deleted."
		).format(signature or _("a threat"), doc.file_name, doc.owner, len(removed)),
		details=details,
	)
	return removed


def _detach(row, signature: str | None) -> None:
	if not (row.attached_to_doctype and row.attached_to_name):
		return
	try:
		if row.attached_to_field and frappe.db.exists(row.attached_to_doctype, row.attached_to_name):
			current = frappe.db.get_value(
				row.attached_to_doctype, row.attached_to_name, row.attached_to_field
			)
			if current == row.file_url:
				frappe.db.set_value(
					row.attached_to_doctype,
					row.attached_to_name,
					row.attached_to_field,
					None,
					update_modified=False,
				)
		ref = frappe.get_doc(row.attached_to_doctype, row.attached_to_name)
		ref.add_comment(
			"Attachment Removed",
			_("{0} was removed automatically because the malware scanner found {1}.").format(
				frappe.bold(row.file_name), signature or _("a threat")
			),
		)
	except frappe.DoesNotExistError:
		frappe.clear_last_message()
	except Exception:
		frappe.log_error(title="Hostel360: could not detach infected file", message=frappe.get_traceback())


def _scanner_unreachable(file_name: str, host: str, port: int, error: str) -> None:
	try:
		frappe.cache.sadd(UNSCANNED_SET, file_name)
		frappe.cache.set(frappe.cache.make_key(SCANNER_DOWN_KEY), "1", ex=7 * 24 * 3600)
		should_alert = frappe.cache.set(
			frappe.cache.make_key(f"{SCANNER_DOWN_KEY}:alerted"), "1", nx=True, ex=SCANNER_ALERT_INTERVAL
		)
	except Exception:
		should_alert = True

	security_log("malware_scanner_unreachable", level="warning", host=host, port=port, file=file_name)
	if should_alert:
		_alert(
			key=SCANNER_ALERT_KEY,
			title=_("Malware scanner unreachable"),
			severity="Warning",
			description=_(
				"Uploads are being accepted without a malware scan because clamd at {0}:{1} cannot be "
				"reached ({2}). They will be scanned once the scanner is back."
			).format(host, port, error[:200]),
			details={"host": host, "port": port, "error": error[:300], "file": file_name},
		)


def _scanner_reachable() -> None:
	try:
		was_down = frappe.cache.get(frappe.cache.make_key(SCANNER_DOWN_KEY))
	except Exception:
		return
	if not was_down:
		return
	frappe.cache.delete(
		frappe.cache.make_key(SCANNER_DOWN_KEY), frappe.cache.make_key(f"{SCANNER_DOWN_KEY}:alerted")
	)
	try:
		from hostel360.hostel_operations.alerts import resolve_alert

		resolve_alert(SCANNER_ALERT_KEY, notes="The malware scanner is reachable again.", source="Security")
	except ImportError:
		pass
	except Exception:
		frappe.log_error(title="Hostel360: could not resolve scanner alert", message=frappe.get_traceback())
	_scan_backlog()


def _scan_backlog() -> None:
	"""Queue scans for files accepted while the scanner was down."""
	for _index in range(BACKLOG_BATCH):
		name = frappe.cache.spop(UNSCANNED_SET)
		if not name:
			break
		name = name.decode() if isinstance(name, bytes) else name
		frappe.enqueue(
			SCAN_METHOD,
			queue="short",
			job_id=f"hostel360-scan-{name}",
			deduplicate=True,
			file_name=name,
		)


def _alert(key: str, title: str, severity: str, description: str, details: dict) -> None:
	try:
		from hostel360.hostel_operations.alerts import raise_alert
	except ImportError:
		frappe.log_error(title=f"Hostel360: {title}", message=description)
		return
	try:
		raise_alert(
			key=key,
			title=title,
			severity=severity,
			category="Security",
			source="Security",
			description=description,
			details=details,
		)
	except Exception:
		frappe.log_error(title="Hostel360: could not raise security alert", message=frappe.get_traceback())


def _hash(value: str | None) -> str | None:
	if not value:
		return None
	import hashlib

	return hashlib.sha256(value.encode()).hexdigest()[:12]
