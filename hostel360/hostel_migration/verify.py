"""Verify an exit export against its manifest.

Accepts the export folder, the ``.tar.gz`` archive, or the encrypted ``.h360enc``
archive. Every file listed in ``manifest.json`` must exist with the recorded size and
SHA-256; files that are not in the manifest are reported too. This module needs no
database, so the institution can verify an export on any machine with Python.
"""

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field

from hostel360.hostel_migration import crypto

MANIFEST = "manifest.json"
EXPECTED_FORMAT = "hostel360-exit-export"
CHUNK = 1024 * 1024


@dataclass
class VerifyResult:
	path: str
	ok: bool = True
	checked: int = 0
	bytes: int = 0
	problems: list[str] = field(default_factory=list)
	export_name: str | None = None
	site: str | None = None
	created_on: str | None = None
	extracted_to: str | None = None
	archive_checksum: str | None = None
	encrypted: bool = False
	# SHA-256 of manifest.json: the export's fingerprint, compared with the site's export log.
	manifest_sha256: str | None = None
	manifest_summary: dict = field(default_factory=dict)

	def fail(self, message: str) -> None:
		self.ok = False
		self.problems.append(message)

	def as_dict(self) -> dict:
		return {
			"path": self.path,
			"ok": self.ok,
			"checked": self.checked,
			"bytes": self.bytes,
			"problems": self.problems,
			"export_name": self.export_name,
			"site": self.site,
			"created_on": self.created_on,
			"extracted_to": self.extracted_to,
			"archive_checksum": self.archive_checksum,
			"encrypted": self.encrypted,
			"manifest_sha256": self.manifest_sha256,
			"manifest_summary": self.manifest_summary,
		}


def _sha256(path: str) -> str:
	digest = hashlib.sha256()
	with open(path, "rb") as f:
		while chunk := f.read(CHUNK):
			digest.update(chunk)
	return digest.hexdigest()


def verify_directory(root: str, result: VerifyResult | None = None) -> VerifyResult:
	result = result or VerifyResult(path=root)
	manifest_path = os.path.join(root, MANIFEST)
	if not os.path.isfile(manifest_path):
		result.fail(f"{MANIFEST} is missing.")
		return result
	try:
		with open(manifest_path, encoding="utf-8") as f:
			manifest = json.load(f)
	except (OSError, ValueError) as e:
		result.fail(f"{MANIFEST} cannot be read: {e}")
		return result
	if manifest.get("format") != EXPECTED_FORMAT:
		result.fail(f"{MANIFEST} is not a Hostel360 exit export manifest.")
		return result

	result.export_name = manifest.get("export_name")
	result.site = manifest.get("site")
	result.created_on = manifest.get("created_on")
	result.manifest_sha256 = _sha256(manifest_path)
	totals = manifest.get("totals") or {}
	result.manifest_summary = {
		"created_by": manifest.get("created_by"),
		"include_files": bool((manifest.get("files") or {}).get("included")),
		"doctypes": len(manifest.get("doctypes") or []),
		"entries": totals.get("entries"),
		"bytes": totals.get("bytes"),
	}

	listed = set()
	real_root = os.path.realpath(root)
	for entry in manifest.get("entries") or []:
		relative = entry.get("path") or ""
		listed.add(relative)
		full = os.path.realpath(os.path.join(root, relative))
		if not full.startswith(real_root + os.sep):
			result.fail(f"{relative}: the manifest points outside the export.")
			continue
		if not os.path.isfile(full):
			result.fail(f"{relative}: missing.")
			continue
		size = os.path.getsize(full)
		if size != entry.get("size"):
			result.fail(f"{relative}: size is {size} bytes, the manifest says {entry.get('size')}.")
			continue
		if _sha256(full) != entry.get("sha256"):
			result.fail(f"{relative}: SHA-256 does not match the manifest.")
			continue
		result.checked += 1
		result.bytes += size

	for folder, _dirs, filenames in os.walk(root):
		for filename in filenames:
			relative = os.path.relpath(os.path.join(folder, filename), root).replace(os.sep, "/")
			if relative != MANIFEST and relative not in listed:
				result.fail(f"{relative}: present but not listed in the manifest.")

	if totals.get("entries") is not None and totals["entries"] != len(manifest.get("entries") or []):
		result.fail("The manifest's entry count does not match its list of files.")
	return result


def _check_sidecar(archive: str, result: VerifyResult) -> None:
	sidecar = archive + ".sha256"
	result.archive_checksum = _sha256(archive)
	if not os.path.isfile(sidecar):
		return
	with open(sidecar) as f:
		expected = (f.read().split() or [""])[0].lower()
	if expected and expected != result.archive_checksum:
		result.fail(f"{os.path.basename(archive)}: SHA-256 does not match {os.path.basename(sidecar)}.")


def _extract(archive: str, destination: str) -> str:
	with tarfile.open(archive, "r:*") as tar:
		# The "data" filter refuses absolute paths, "..", links outside the folder and
		# device files, so a tampered archive cannot write outside ``destination``.
		tar.extractall(destination, filter="data")
	entries = [e for e in os.listdir(destination) if not e.startswith(".")]
	if len(entries) == 1 and os.path.isdir(os.path.join(destination, entries[0])):
		return os.path.join(destination, entries[0])
	return destination


def verify_export(path: str, passphrase: str | None = None, extract_to: str | None = None) -> VerifyResult:
	"""Verify a folder, archive or encrypted archive. ``extract_to`` keeps the extracted copy."""
	path = os.path.abspath(path)
	result = VerifyResult(path=path)
	if os.path.isdir(path):
		return verify_directory(path, result)
	if not os.path.isfile(path):
		result.fail("The path does not exist.")
		return result

	_check_sidecar(path, result)
	work = extract_to or tempfile.mkdtemp(prefix="h360-verify-")
	os.makedirs(work, exist_ok=True)
	try:
		archive = path
		if crypto.is_encrypted(path):
			result.encrypted = True
			if not passphrase:
				result.fail("The export is encrypted; a passphrase is required.")
				return result
			archive = os.path.join(work, ".decrypted.tar.gz")
			try:
				crypto.decrypt_file(path, archive, passphrase)
			except crypto.DecryptionError as e:
				result.fail(str(e))
				return result
		try:
			root = _extract(archive, work)
		except (tarfile.TarError, OSError) as e:
			result.fail(f"The archive cannot be unpacked: {e}")
			return result
		finally:
			if archive != path and os.path.exists(archive):
				os.remove(archive)
		verify_directory(root, result)
		if extract_to:
			result.extracted_to = root
		return result
	finally:
		if not extract_to:
			shutil.rmtree(work, ignore_errors=True)
