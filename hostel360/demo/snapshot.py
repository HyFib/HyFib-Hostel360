"""The golden snapshot of a demonstration site: ``sites/SITE/private/demo/``.

``take_snapshot`` writes the whole database (a ``mariadb-dump`` through Frappe's backup
code), the public and private files, and a manifest recording when it was taken, the app
versions and the demo keys of site_config.json. ``restore_snapshot`` puts all of it back:
the dump is loaded with the site's own database user (every table is dropped and created
again), uploaded files are replaced by the snapshot's, and nothing a visitor added survives.

The folder sits outside ``private/files``, so it is never served and never included in
the snapshot itself. Files are written next to their final names and renamed into place,
so a snapshot that fails half-way leaves the previous one intact.
"""

import hashlib
import json
import os
import shutil
import tarfile

import frappe
from frappe import _
from frappe.utils import cstr, now_datetime

MANIFEST = "manifest.json"
DATABASE = "database.sql.gz"
FILE_ARCHIVES = {"public": "public-files.tar.gz", "private": "private-files.tar.gz"}
MANIFEST_VERSION = 1


class SnapshotError(frappe.ValidationError):
	pass


def snapshot_dir() -> str:
	return os.path.abspath(frappe.get_site_path("private", "demo"))


def read_manifest() -> dict | None:
	path = os.path.join(snapshot_dir(), MANIFEST)
	if not os.path.isfile(path):
		return None
	with open(path, encoding="utf-8") as f:
		manifest = json.load(f)
	return manifest if isinstance(manifest, dict) and manifest.get("taken_at") else None


def app_versions() -> dict:
	"""Installed apps with their version and a fingerprint of their schema and patches.

	A restore into code whose schema differs from the snapshot's needs a migrate. The
	version alone does not tell (production images drop git metadata, and a release can
	change a DocType without changing the version), so the fingerprint covers every DocType
	definition's ``modified`` stamp and the patch list.
	"""
	versions = {}
	for app in frappe.get_installed_apps():
		try:
			version = cstr(frappe.get_attr(f"{app}.__version__"))
		except Exception:
			version = ""
		versions[app] = f"{version} {schema_fingerprint(app)}".strip()
	return versions


def schema_fingerprint(app: str) -> str:
	digest = hashlib.sha256()
	root = frappe.get_app_path(app)
	for folder, subfolders, files in os.walk(root):
		subfolders[:] = sorted(d for d in subfolders if d not in ("node_modules", "public", "__pycache__"))
		for name in sorted(files):
			path = os.path.join(folder, name)
			if name == "patches.txt":
				with open(path, "rb") as f:
					digest.update(f.read())
			elif name.endswith(".json") and os.path.basename(folder) + ".json" == name and "doctype" in path:
				try:
					with open(path, encoding="utf-8") as f:
						definition = json.load(f)
				except ValueError:
					continue
				if isinstance(definition, dict) and definition.get("doctype") == "DocType":
					digest.update(f"{definition.get('name')}|{definition.get('modified')}\n".encode())
	return digest.hexdigest()[:16]


def take_snapshot(config: dict) -> dict:
	"""Snapshot the site as it is now. ``config`` is the demo keys to re-apply after a restore."""
	from frappe.utils.backups import BackupGenerator

	folder = snapshot_dir()
	os.makedirs(folder, mode=0o700, exist_ok=True)
	os.chmod(folder, 0o700)
	frappe.db.commit()  # nosemgrep: the dump must see everything prepared so far

	partial = {name: os.path.join(folder, f".{name}.partial") for name in (DATABASE, *FILE_ARCHIVES.values())}
	for path in partial.values():
		if os.path.exists(path):
			os.remove(path)
	try:
		dump = BackupGenerator(
			frappe.conf.db_name,
			frappe.conf.db_user,
			frappe.conf.db_password,
			db_socket=frappe.conf.db_socket,
			db_host=frappe.conf.db_host,
			db_port=frappe.conf.db_port,
			db_type=frappe.conf.db_type,
			backup_path_db=partial[DATABASE],
			ignore_conf=True,
		)
		dump.take_dump()
		for folder_name, archive in FILE_ARCHIVES.items():
			_archive_files(folder_name, partial[archive])

		manifest = {
			"version": MANIFEST_VERSION,
			"site": frappe.local.site,
			"taken_at": str(now_datetime()),
			"apps": app_versions(),
			"config": config,
			"files": {
				name: {"bytes": os.path.getsize(partial[name]), "sha256": _sha256(partial[name])}
				for name in partial
			},
		}
		for name, path in partial.items():
			# The dump holds password hashes and the demo keys: for the site's own user only.
			os.chmod(path, 0o600)
			os.replace(path, os.path.join(folder, name))
		_write_json(os.path.join(folder, MANIFEST), manifest)
	finally:
		for path in partial.values():
			if os.path.exists(path):
				os.remove(path)
	return manifest


def restore_snapshot(manifest: dict) -> None:
	"""Load the snapshot's database and files over the site (see the module docstring)."""
	from frappe.database.mariadb.setup_db import import_db_from_sql

	if frappe.conf.db_type not in (None, "", "mariadb"):
		raise SnapshotError(_("Demonstration snapshots need a MariaDB site."))
	folder = snapshot_dir()
	for name, info in (manifest.get("files") or {}).items():
		path = os.path.join(folder, name)
		if not os.path.isfile(path) or _sha256(path) != info.get("sha256"):
			raise SnapshotError(
				_(
					"The demonstration snapshot file {0} is missing or damaged. Run h360-demo-site again."
				).format(name)
			)

	# Release this connection's table locks first: loading the dump drops and creates every
	# table, and would wait behind them.
	frappe.db.commit()  # nosemgrep
	import_db_from_sql(os.path.join(folder, DATABASE))
	frappe.db.close()
	frappe.connect()

	for folder_name, archive in FILE_ARCHIVES.items():
		_restore_files(folder_name, os.path.join(folder, archive))


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


def _write_json(path: str, value: dict) -> None:
	temporary = f"{path}.partial"
	with open(temporary, "w", encoding="utf-8") as f:
		json.dump(value, f, indent=1, sort_keys=True, default=str)
	os.chmod(temporary, 0o600)
	os.replace(temporary, path)
