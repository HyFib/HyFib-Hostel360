"""Verification of exit exports against their manifest (no database needed)."""

import hashlib
import json
import os
import shutil
import tarfile
import tempfile

from frappe.tests import UnitTestCase

from hostel360.hostel_migration import crypto
from hostel360.hostel_migration.verify import verify_directory, verify_export

PASSPHRASE = "exit export check phrase"


def make_export(root: str, files: dict[str, bytes]) -> dict:
	os.makedirs(root)
	entries = []
	for relative, content in files.items():
		path = os.path.join(root, relative)
		os.makedirs(os.path.dirname(path), exist_ok=True)
		with open(path, "wb") as f:
			f.write(content)
		entries.append(
			{"path": relative, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
		)
	manifest = {
		"format": "hostel360-exit-export",
		"format_version": 1,
		"export_name": os.path.basename(root),
		"site": "test.localhost",
		"created_on": "2026-10-15T10:00:00",
		"totals": {"entries": len(entries), "bytes": sum(e["size"] for e in entries)},
		"entries": entries,
	}
	with open(os.path.join(root, "manifest.json"), "w") as f:
		json.dump(manifest, f)
	return manifest


class TestVerifyExport(UnitTestCase):
	def setUp(self):
		self.folder = tempfile.mkdtemp(prefix="h360-verify-test-")
		self.addCleanup(shutil.rmtree, self.folder, True)
		self.root = os.path.join(self.folder, "hostel360-export-test")
		make_export(
			self.root,
			{
				"data/csv/Hostel.csv": b"name,hostel_name\nBH1,Boys Hostel I\n",
				"data/csv/Hostel Student.csv": "name,full_name\nS1,அருண்\n".encode(),
				"database/site.sql.gz": os.urandom(2048),
				"README.txt": b"readme",
			},
		)

	def pack(self) -> str:
		archive = self.root + ".tar.gz"
		with tarfile.open(archive, "w:gz") as tar:
			tar.add(self.root, arcname=os.path.basename(self.root))
		return archive

	def test_intact_folder(self):
		result = verify_directory(self.root)
		self.assertTrue(result.ok, result.problems)
		self.assertEqual(result.checked, 4)
		self.assertEqual(result.export_name, "hostel360-export-test")

	def test_changed_missing_and_extra_files(self):
		with open(os.path.join(self.root, "data/csv/Hostel.csv"), "r+b") as f:
			f.write(b"X")  # same size, different content
		os.remove(os.path.join(self.root, "README.txt"))
		with open(os.path.join(self.root, "data/csv/Unlisted.csv"), "wb") as f:
			f.write(b"x")
		result = verify_directory(self.root)
		self.assertFalse(result.ok)
		problems = "\n".join(result.problems)
		self.assertIn("Hostel.csv: SHA-256 does not match", problems)
		self.assertIn("README.txt: missing", problems)
		self.assertIn("Unlisted.csv: present but not listed", problems)

	def test_manifest_cannot_point_outside(self):
		path = os.path.join(self.root, "manifest.json")
		with open(path) as f:
			manifest = json.load(f)
		manifest["entries"].append({"path": "../../etc/passwd", "size": 1, "sha256": "0" * 64})
		manifest["totals"]["entries"] += 1
		with open(path, "w") as f:
			json.dump(manifest, f)
		result = verify_directory(self.root)
		self.assertFalse(result.ok)
		self.assertIn("outside the export", "\n".join(result.problems))

	def test_not_an_export(self):
		with open(os.path.join(self.root, "manifest.json"), "w") as f:
			json.dump({"format": "something-else"}, f)
		self.assertFalse(verify_directory(self.root).ok)
		os.remove(os.path.join(self.root, "manifest.json"))
		self.assertFalse(verify_directory(self.root).ok)

	def test_archive_with_sidecar(self):
		archive = self.pack()
		with open(archive + ".sha256", "w") as f:
			f.write(hashlib.sha256(open(archive, "rb").read()).hexdigest() + "  x.tar.gz\n")
		result = verify_export(archive)
		self.assertTrue(result.ok, result.problems)
		self.assertEqual(result.checked, 4)

		with open(archive + ".sha256", "w") as f:
			f.write("0" * 64 + "  x.tar.gz\n")
		result = verify_export(archive)
		self.assertFalse(result.ok)

	def test_encrypted_archive(self):
		archive = self.pack()
		sealed = archive + crypto.ENCRYPTED_SUFFIX
		crypto.encrypt_file(archive, sealed, PASSPHRASE, chunk_size=1024, log2_n=14)

		self.assertFalse(verify_export(sealed).ok)  # no passphrase
		self.assertFalse(verify_export(sealed, passphrase="not the right phrase").ok)

		extract_to = os.path.join(self.folder, "restored")
		result = verify_export(sealed, passphrase=PASSPHRASE, extract_to=extract_to)
		self.assertTrue(result.ok, result.problems)
		self.assertTrue(os.path.isfile(os.path.join(result.extracted_to, "data", "csv", "Hostel.csv")))
		# The decrypted archive itself is not left lying around.
		self.assertEqual([n for n in os.listdir(extract_to) if n.endswith(".tar.gz")], [])

	def test_archive_with_unsafe_member_is_refused(self):
		evil = os.path.join(self.folder, "evil.tar.gz")
		payload = os.path.join(self.folder, "payload.txt")
		with open(payload, "w") as f:
			f.write("x")
		with tarfile.open(evil, "w:gz") as tar:
			tar.add(payload, arcname="../../escaped.txt")
		result = verify_export(evil)
		self.assertFalse(result.ok)
		self.assertFalse(os.path.exists(os.path.join(self.folder, "..", "escaped.txt")))
