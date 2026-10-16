"""AES-256-GCM encryption of exit-export archives."""

import os
import shutil
import tempfile

from frappe.tests import UnitTestCase

from hostel360.hostel_migration import crypto

PASSPHRASE = "Karaikudi hostel records 2026"
# The cheapest scrypt cost the format allows keeps these tests fast.
FAST = {"log2_n": 14, "r": 8, "p": 1}


class TestCrypto(UnitTestCase):
	def setUp(self):
		self.folder = tempfile.mkdtemp(prefix="h360-crypto-test-")
		self.addCleanup(shutil.rmtree, self.folder, True)
		self.plain = os.path.join(self.folder, "plain.bin")
		self.sealed = os.path.join(self.folder, "plain.bin.h360enc")
		self.opened = os.path.join(self.folder, "opened.bin")
		self.data = os.urandom(10_000) + b"hostel" * 1000

	def encrypt(self, data: bytes | None = None, chunk_size: int = 4096):
		with open(self.plain, "wb") as f:
			f.write(self.data if data is None else data)
		crypto.encrypt_file(self.plain, self.sealed, PASSPHRASE, chunk_size=chunk_size, **FAST)

	def read(self, path):
		with open(path, "rb") as f:
			return f.read()

	def test_round_trip_across_chunks(self):
		self.encrypt()
		self.assertTrue(crypto.is_encrypted(self.sealed))
		self.assertFalse(crypto.is_encrypted(self.plain))
		self.assertNotIn(b"hostel" * 10, self.read(self.sealed))
		crypto.decrypt_file(self.sealed, self.opened, PASSPHRASE)
		self.assertEqual(self.read(self.opened), self.data)

	def test_exact_multiple_and_empty_files(self):
		for data in (b"x" * 8192, b""):
			self.encrypt(data)
			crypto.decrypt_file(self.sealed, self.opened, PASSPHRASE)
			self.assertEqual(self.read(self.opened), data)

	def test_wrong_passphrase(self):
		self.encrypt()
		with self.assertRaises(crypto.DecryptionError) as ctx:
			crypto.decrypt_file(self.sealed, self.opened, "Karaikudi hostel records 2025")
		self.assertIn("passphrase", str(ctx.exception))
		self.assertFalse(os.path.exists(self.opened))
		self.assertFalse(os.path.exists(self.opened + ".part"))

	def test_altered_byte_is_detected(self):
		self.encrypt()
		sealed = bytearray(self.read(self.sealed))
		sealed[crypto.HEADER.size + 4 + 4096 + 50] ^= 0x01
		with open(self.sealed, "wb") as f:
			f.write(sealed)
		with self.assertRaises(crypto.DecryptionError):
			crypto.decrypt_file(self.sealed, self.opened, PASSPHRASE)
		self.assertFalse(os.path.exists(self.opened))

	def test_header_tampering_is_detected(self):
		self.encrypt()
		sealed = bytearray(self.read(self.sealed))
		sealed[20] ^= 0x01  # inside the salt: authenticated as associated data
		with open(self.sealed, "wb") as f:
			f.write(sealed)
		with self.assertRaises(crypto.DecryptionError):
			crypto.decrypt_file(self.sealed, self.opened, PASSPHRASE)

	def test_dropping_the_last_chunk_is_detected(self):
		self.encrypt(chunk_size=4096)
		sealed = self.read(self.sealed)
		# Walk the records and cut the file after the second-last one: every chunk left is
		# intact, but the stream no longer ends with a chunk sealed as "last".
		offset, records = crypto.HEADER.size, []
		while offset < len(sealed):
			(length,) = crypto.LENGTH.unpack(sealed[offset : offset + 4])
			records.append(offset)
			offset += 4 + length
		with open(self.sealed, "wb") as f:
			f.write(sealed[: records[-1]])
		with self.assertRaises(crypto.DecryptionError) as ctx:
			crypto.decrypt_file(self.sealed, self.opened, PASSPHRASE)
		self.assertIn("truncated or altered", str(ctx.exception))

	def test_reordered_chunks_are_detected(self):
		self.encrypt(chunk_size=4096)
		sealed = self.read(self.sealed)
		header = sealed[: crypto.HEADER.size]
		body = sealed[crypto.HEADER.size :]
		first_len = 4 + crypto.LENGTH.unpack(body[:4])[0]
		second_len = 4 + crypto.LENGTH.unpack(body[first_len : first_len + 4])[0]
		swapped = body[first_len : first_len + second_len] + body[:first_len] + body[first_len + second_len :]
		with open(self.sealed, "wb") as f:
			f.write(header + swapped)
		with self.assertRaises(crypto.DecryptionError):
			crypto.decrypt_file(self.sealed, self.opened, PASSPHRASE)

	def test_not_an_encrypted_file(self):
		with open(self.plain, "wb") as f:
			f.write(b"PK\x03\x04 just a zip")
		with self.assertRaises(crypto.DecryptionError):
			crypto.decrypt_file(self.plain, self.opened, PASSPHRASE)

	def test_passphrase_policy(self):
		for weak in ("", "short", "aaaaaaaaaaaaaaaa"):
			with self.assertRaises(ValueError):
				crypto.check_passphrase(weak)
		crypto.check_passphrase(PASSPHRASE)
		with self.assertRaises(ValueError):
			crypto.encrypt_file(self.plain, self.sealed, "short")

	def test_each_encryption_is_unique(self):
		self.encrypt()
		first = self.read(self.sealed)
		self.encrypt()
		self.assertNotEqual(first, self.read(self.sealed))
