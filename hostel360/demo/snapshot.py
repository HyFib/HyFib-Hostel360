"""The golden snapshot of a demonstration site: ``sites/SITE/private/demo/``.

``take_snapshot`` writes the whole database (a ``mariadb-dump`` through Frappe's backup
code), the public and private files, and a manifest recording when it was taken, the app
versions and the demo keys of site_config.json. ``restore_snapshot`` puts all of it back:
the dump is loaded with the site's own database user (every table is dropped and created
again), uploaded files are replaced by the snapshot's, and nothing a visitor added survives.

Each snapshot is a generation: a folder ``g-<time>-<random>`` holding the dump, the two file
archives and the manifest, and the file ``current`` names the generation in use. A new
generation is written and flushed to disk completely before ``current`` is switched to it
with one rename, and older generations are removed only after that, so a snapshot that fails
or is killed at any point leaves the previous one whole. A ``current`` that names nothing
usable is refused, never read as "no snapshot": h360-demo-site would then snapshot whatever
visitors left on the site. Snapshots from earlier releases (the same four files directly in
the folder) are still read and restored; the first generation written replaces them.

The folder sits outside ``private/files``, so it is never served and never included in the
snapshot itself.
"""

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import tarfile

import frappe
from frappe import _
from frappe.utils import cstr, now_datetime

MANIFEST = "manifest.json"
DATABASE = "database.sql.gz"
FILE_ARCHIVES = {"public": "public-files.tar.gz", "private": "private-files.tar.gz"}
SNAPSHOT_FILES = (DATABASE, *FILE_ARCHIVES.values())
MANIFEST_VERSION = 2
POINTER = "current"
# Shell-safe on purpose: Frappe puts the dump's path into shell commands without quoting it.
GENERATION = re.compile(r"g-\d{8}T\d{6}-[0-9a-f]{8}")


class SnapshotError(frappe.ValidationError):
	pass


def snapshot_dir() -> str:
	return os.path.abspath(frappe.get_site_path("private", "demo"))


def damaged(root: str) -> SnapshotError:
	return SnapshotError(
		_(
			"The demonstration snapshot in {0} is damaged or incomplete, so nothing was restored and the "
			"site was left as it is. Put the folder back from a backup of the demo host, or start the "
			"demonstration over on a new site (deploy/demo/README.md, Start over)."
		).format(root)
	)


def _pointer_name(root: str) -> str | None:
	"""The generation ``current`` names, as written (None when there is no pointer)."""
	try:
		with open(os.path.join(root, POINTER), encoding="utf-8") as f:
			return f.read().strip()
	except FileNotFoundError:
		return None
	except (OSError, ValueError) as e:
		raise damaged(root) from e


def generation_folder(root: str, name: str | None) -> str:
	"""The folder of generation ``name``, which must exist and hold a manifest."""
	folder = os.path.join(root, name or "")
	if not GENERATION.fullmatch(name or "") or not os.path.isfile(os.path.join(folder, MANIFEST)):
		raise damaged(root)
	return folder


def current_folder() -> str | None:
	"""The folder of the snapshot in use, or None when the site has never had one."""
	root = snapshot_dir()
	name = _pointer_name(root)
	if name is None:
		# A snapshot from an earlier release keeps its files in the folder itself.
		return root if os.path.isfile(os.path.join(root, MANIFEST)) else None
	return generation_folder(root, name)


def read_manifest() -> dict | None:
	"""The manifest of the snapshot in use; None only when there is no snapshot at all."""
	folder = current_folder()
	if folder is None:
		return None
	root = snapshot_dir()
	try:
		with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
			manifest = json.load(f)
	except ValueError as e:
		raise damaged(root) from e
	if not isinstance(manifest, dict) or not manifest.get("taken_at"):
		raise damaged(root)
	# Which folder the restore reads, decided once: a snapshot taken meanwhile cannot change it.
	manifest["generation"] = None if folder == root else os.path.basename(folder)
	return manifest


def manifest_folder(manifest: dict) -> str:
	root = snapshot_dir()
	name = manifest.get("generation")
	if not name:
		if not os.path.isfile(os.path.join(root, MANIFEST)):
			raise damaged(root)
		return root
	return generation_folder(root, name)


def verify_snapshot(manifest: dict) -> str:
	"""Check every file of the snapshot against its manifest; returns the snapshot's folder."""
	folder = manifest_folder(manifest)
	files = manifest.get("files") or {}
	for name in SNAPSHOT_FILES:
		path = os.path.join(folder, name)
		info = files.get(name) or {}
		if not info.get("sha256") or not os.path.isfile(path) or _sha256(path) != info["sha256"]:
			raise SnapshotError(
				_(
					"The demonstration snapshot file {0} in {1} is missing or damaged; nothing was restored. "
					"Put the folder back from a backup of the demo host, or start the demonstration over on "
					"a new site (deploy/demo/README.md, Start over)."
				).format(name, folder)
			)
	return folder


def app_versions() -> dict:
	"""Installed apps with their version and a fingerprint of what ``bench migrate`` applies.

	A restore into code that differs from the snapshot's needs a migrate. The version alone
	does not tell (production images drop git metadata, and a release can change a DocType or
	a workspace without changing the version), so the fingerprint covers the files a migrate
	reads (see ``sync_fingerprint``).
	"""
	versions = {}
	for app in frappe.get_installed_apps():
		try:
			version = cstr(frappe.get_attr(f"{app}.__version__"))
		except Exception:
			version = ""
		versions[app] = f"{version} {sync_fingerprint(app)}".strip()
	return versions


# Besides every JSON record, the files whose changes a migrate applies to the database:
# modules (Module Def rows), patches, and hooks (scheduled jobs, fixtures, roles).
SYNCED_FILES = ("modules.txt", "patches.txt", "hooks.py")
SKIPPED_FOLDERS = ("node_modules", "public", "__pycache__", "locale")


def sync_fingerprint(app: str) -> str:
	"""A digest of every file whose change ``bench migrate`` would carry into the database.

	Migrate imports far more than DocTypes: workspaces, sidebars, the dock, desktop icons,
	number cards, charts, reports, pages, print formats and onboarding are all JSON records
	in the app, and modules come from modules.txt. Restoring a snapshot taken before such a
	change brings the old records back, so a change to any of them must be seen here, or the
	demonstration keeps showing the old desk after every reset. The content is hashed, not
	the ``modified`` stamps, so an edit that forgot to touch its stamp still counts.
	"""
	digest = hashlib.sha256()
	root = frappe.get_app_path(app)
	for folder, subfolders, files in os.walk(root):
		subfolders[:] = sorted(d for d in subfolders if d not in SKIPPED_FOLDERS)
		for name in sorted(files):
			if not (name.endswith(".json") or name in SYNCED_FILES):
				continue
			path = os.path.join(folder, name)
			digest.update(os.path.relpath(path, root).encode() + b"\0")
			with open(path, "rb") as f:
				digest.update(hashlib.sha256(f.read()).digest())
	return digest.hexdigest()[:16]


def take_snapshot(config: dict) -> dict:
	"""Snapshot the site as it is now. ``config`` is the demo keys to re-apply after a restore."""
	from frappe.utils.backups import BackupGenerator

	root = snapshot_dir()
	os.makedirs(root, mode=0o700, exist_ok=True)
	os.chmod(root, 0o700)
	# Refuses a damaged pointer before anything is deleted; then clears what a failed or
	# killed snapshot left behind.
	in_use = current_folder()
	_remove_unused(root, keep=in_use, legacy=False)
	frappe.db.commit()  # nosemgrep: the dump must see everything prepared so far

	name = f"g-{now_datetime():%Y%m%dT%H%M%S}-{secrets.token_hex(4)}"
	folder = os.path.join(root, name)
	os.mkdir(folder, 0o700)
	paths = {file_name: os.path.join(folder, file_name) for file_name in SNAPSHOT_FILES}
	switched = False
	try:
		dump = BackupGenerator(
			frappe.conf.db_name,
			frappe.conf.db_user,
			frappe.conf.db_password,
			db_socket=frappe.conf.db_socket,
			db_host=frappe.conf.db_host,
			db_port=frappe.conf.db_port,
			db_type=frappe.conf.db_type,
			backup_path_db=paths[DATABASE],
			ignore_conf=True,
		)
		dump.take_dump()
		for folder_name, archive in FILE_ARCHIVES.items():
			_archive_files(folder_name, paths[archive])
		for path in paths.values():
			# The dump holds password hashes and the demo keys: for the site's own user only.
			os.chmod(path, 0o600)
			_flush(path)

		manifest = {
			"version": MANIFEST_VERSION,
			"site": frappe.local.site,
			"generation": name,
			"taken_at": str(now_datetime()),
			"apps": app_versions(),
			"config": config,
			"files": {
				file_name: {"bytes": os.path.getsize(path), "sha256": _sha256(path)}
				for file_name, path in paths.items()
			},
		}
		_write_file(
			os.path.join(folder, MANIFEST), json.dumps(manifest, indent=1, sort_keys=True, default=str)
		)
		_flush(folder)
		# The switch: one rename. Until it happens the previous snapshot is the one in use.
		_write_file(os.path.join(root, POINTER), f"{name}\n")
		switched = True
	finally:
		if not switched and _raw_pointer(root) != name:
			shutil.rmtree(folder, ignore_errors=True)
	try:
		_flush(root)
	except OSError:
		# The new generation is already the one in use; only how durably that is recorded is
		# in doubt, and the next snapshot flushes the folder again.
		frappe.logger("hostel360").warning("Demonstration snapshot: could not flush %s", root, exc_info=True)
	_remove_unused(root, keep=folder, legacy=True)
	return manifest


def restore_snapshot(manifest: dict) -> None:
	"""Load the snapshot's database and files over the site (see the module docstring)."""
	from frappe.database.mariadb.setup_db import import_db_from_sql

	if frappe.conf.db_type not in (None, "", "mariadb"):
		raise SnapshotError(_("Demonstration snapshots need a MariaDB site."))
	folder = verify_snapshot(manifest)

	# Release this connection's table locks first: loading the dump drops and creates every
	# table, and would wait behind them.
	frappe.db.commit()  # nosemgrep
	import_db_from_sql(os.path.join(folder, DATABASE))
	frappe.db.close()
	frappe.connect()

	for folder_name, archive in FILE_ARCHIVES.items():
		_restore_files(folder_name, os.path.join(folder, archive))


def _raw_pointer(root: str) -> str | None:
	"""``current`` as written, without judging it (used while cleaning up after an error)."""
	try:
		return _pointer_name(root)
	except SnapshotError:
		return None


def _remove_unused(root: str, keep: str | None, legacy: bool) -> None:
	"""Delete generations other than ``keep``, temporary files and, with ``legacy``, the files a
	snapshot from an earlier release kept directly in the folder."""
	for entry in os.scandir(root):
		if entry.name.endswith(".partial") and entry.is_file(follow_symlinks=False):
			os.remove(entry.path)
		elif GENERATION.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False):
			if entry.path != keep:
				shutil.rmtree(entry.path)
		elif legacy and keep != root and entry.name in (MANIFEST, *SNAPSHOT_FILES):
			os.remove(entry.path)


def _archive_files(folder_name: str, target: str) -> None:
	source = os.path.abspath(frappe.get_site_path(folder_name, "files"))
	with tarfile.open(target, "w:gz") as archive:
		if os.path.isdir(source):
			archive.add(source, arcname=f"{folder_name}/files")


def _restore_files(folder_name: str, archive_path: str) -> None:
	site_path = os.path.abspath(frappe.get_site_path())
	current = os.path.join(site_path, folder_name, "files")
	aside = f"{current}.before-demo-reset"
	if os.path.exists(aside):
		shutil.rmtree(aside)
	if os.path.exists(current):
		os.replace(current, aside)
	try:
		with tarfile.open(archive_path, "r:gz") as archive:
			# The "data" filter refuses absolute paths, links out of the site and device files.
			archive.extractall(site_path, filter="data")
		os.makedirs(current, exist_ok=True)
	except Exception:
		if os.path.exists(current):
			shutil.rmtree(current)
		if os.path.exists(aside):
			os.replace(aside, current)
		raise
	if os.path.exists(aside):
		shutil.rmtree(aside)


def _sha256(path: str) -> str:
	digest = hashlib.sha256()
	with open(path, "rb") as f:
		for block in iter(lambda: f.read(1024 * 1024), b""):
			digest.update(block)
	return digest.hexdigest()


def _flush(path: str) -> None:
	"""fsync a file or a folder, so a rename that follows cannot land before the data does."""
	fd = os.open(path, os.O_RDONLY)
	try:
		os.fsync(fd)
	finally:
		os.close(fd)


def _write_file(path: str, text: str) -> None:
	"""Write ``path`` whole or not at all: a flushed temporary file renamed over it."""
	temporary = f"{path}.partial"
	fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as f:
			f.write(text)
			f.flush()
			os.fsync(f.fileno())
		os.chmod(temporary, 0o600)
		os.replace(temporary, path)
	except BaseException:
		with contextlib.suppress(OSError):
			os.remove(temporary)
		raise
