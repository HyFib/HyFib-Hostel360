"""Passphrase encryption for exit-export archives (AES-256-GCM, scrypt key).

Exports can be many gigabytes, so the archive is encrypted in 4 MiB chunks, each sealed
with AES-256-GCM. The chunk number and a "last chunk" flag are part of every nonce and
the file header is authenticated with every chunk, so reordering, truncating, splicing
or editing any byte is detected on decryption.

File layout (all integers big-endian)::

    magic      8 bytes  b"H360ENC1"
    version    1 byte   1
    kdf        1 byte   1 = scrypt
    log2_n     1 byte   scrypt cost (n = 2 ** log2_n)
    r          1 byte   scrypt block size
    p          1 byte   scrypt parallelism
    salt      16 bytes  random
    prefix     7 bytes  random nonce prefix
    chunk      4 bytes  plaintext chunk size
    then for each chunk: length (4 bytes) + ciphertext with 16-byte GCM tag

    nonce(i) = prefix (7) || i (4) || last (1: 0x01 for the final chunk, else 0x00)

The same format can be decrypted without Hostel360; ``README.md`` has a short standalone
Python snippet for the institution's records.
"""

import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"H360ENC1"
FORMAT_VERSION = 1
KDF_SCRYPT = 1
HEADER = struct.Struct(">8sBBBBB16s7sI")
LENGTH = struct.Struct(">I")
TAG_BYTES = 16
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024
DEFAULT_LOG2_N = 17  # 128 MiB of memory, about half a second: slow for guessing, fine once
DEFAULT_R = 8
DEFAULT_P = 1
MIN_PASSPHRASE_LENGTH = 12
ENCRYPTED_SUFFIX = ".h360enc"


class DecryptionError(ValueError):
	"""Wrong passphrase, or the file was damaged or altered."""


def check_passphrase(passphrase: str) -> None:
	if not passphrase or len(passphrase) < MIN_PASSPHRASE_LENGTH:
		raise ValueError(f"Use a passphrase of at least {MIN_PASSPHRASE_LENGTH} characters.")
	if len(set(passphrase)) < 5:
		raise ValueError("The passphrase is too repetitive; use a longer phrase with varied characters.")


def derive_key(passphrase: str, salt: bytes, log2_n: int, r: int, p: int) -> bytes:
	if not 14 <= log2_n <= 22 or not 1 <= r <= 32 or not 1 <= p <= 16:
		raise DecryptionError("The file header has implausible key-derivation settings.")
	return Scrypt(salt=salt, length=32, n=2**log2_n, r=r, p=p).derive(passphrase.encode("utf-8"))


def _nonce(prefix: bytes, index: int, last: bool) -> bytes:
	if index >= 2**32:
		raise ValueError("The file is too large for this format.")
	return prefix + struct.pack(">I", index) + (b"\x01" if last else b"\x00")


def encrypt_file(
	source: str,
	destination: str,
	passphrase: str,
	chunk_size: int = DEFAULT_CHUNK_SIZE,
	log2_n: int = DEFAULT_LOG2_N,
	r: int = DEFAULT_R,
	p: int = DEFAULT_P,
) -> None:
	check_passphrase(passphrase)
	salt, prefix = os.urandom(16), os.urandom(7)
	header = HEADER.pack(MAGIC, FORMAT_VERSION, KDF_SCRYPT, log2_n, r, p, salt, prefix, chunk_size)
	aead = AESGCM(derive_key(passphrase, salt, log2_n, r, p))

	tmp = destination + ".part"
	try:
		with open(source, "rb") as src, open(tmp, "wb") as out:
			os.chmod(tmp, 0o600)
			out.write(header)
			index = 0
			chunk = src.read(chunk_size)
			while True:
				following = src.read(chunk_size)
				last = not following
				sealed = aead.encrypt(_nonce(prefix, index, last), chunk, header)
				out.write(LENGTH.pack(len(sealed)))
				out.write(sealed)
				if last:
					break
				chunk, index = following, index + 1
			out.flush()
			os.fsync(out.fileno())
		os.replace(tmp, destination)
	finally:
		if os.path.exists(tmp):
			os.remove(tmp)


def read_header(handle) -> tuple[bytes, dict]:
	raw = handle.read(HEADER.size)
	if len(raw) != HEADER.size:
		raise DecryptionError("This is not a Hostel360 encrypted export (file too short).")
	magic, version, kdf, log2_n, r, p, salt, prefix, chunk_size = HEADER.unpack(raw)
	if magic != MAGIC:
		raise DecryptionError("This is not a Hostel360 encrypted export.")
	if version != FORMAT_VERSION or kdf != KDF_SCRYPT:
		raise DecryptionError(f"Unsupported encrypted export format (version {version}, kdf {kdf}).")
	if not 1024 <= chunk_size <= 64 * 1024 * 1024:
		raise DecryptionError("The file header has an implausible chunk size.")
	return raw, {"log2_n": log2_n, "r": r, "p": p, "salt": salt, "prefix": prefix, "chunk_size": chunk_size}


def _read_record(handle, chunk_size: int) -> bytes | None:
	raw = handle.read(LENGTH.size)
	if not raw:
		return None
	if len(raw) != LENGTH.size:
		raise DecryptionError("The encrypted file is truncated.")
	(length,) = LENGTH.unpack(raw)
	if not TAG_BYTES <= length <= chunk_size + TAG_BYTES:
		raise DecryptionError("The encrypted file is damaged (bad chunk length).")
	sealed = handle.read(length)
	if len(sealed) != length:
		raise DecryptionError("The encrypted file is truncated.")
	return sealed


def decrypt_file(source: str, destination: str, passphrase: str) -> None:
	"""Decrypt and authenticate ``source``. Nothing is left at ``destination`` on failure."""
	tmp = destination + ".part"
	try:
		with open(source, "rb") as src:
			header, params = read_header(src)
			aead = AESGCM(derive_key(passphrase, params["salt"], params["log2_n"], params["r"], params["p"]))
			with open(tmp, "wb") as out:
				os.chmod(tmp, 0o600)
				index = 0
				sealed = _read_record(src, params["chunk_size"])
				if sealed is None:
					raise DecryptionError("The encrypted file has no content.")
				while sealed is not None:
					following = _read_record(src, params["chunk_size"])
					last = following is None
					try:
						out.write(aead.decrypt(_nonce(params["prefix"], index, last), sealed, header))
					except InvalidTag as e:
						if index == 0:
							raise DecryptionError(
								"Decryption failed: the passphrase is wrong or the file was altered."
							) from e
						raise DecryptionError(
							f"Decryption failed at chunk {index + 1}: the file was truncated or altered."
						) from e
					sealed, index = following, index + 1
		os.replace(tmp, destination)
	finally:
		if os.path.exists(tmp):
			os.remove(tmp)


def is_encrypted(path: str) -> bool:
	try:
		with open(path, "rb") as f:
			return f.read(len(MAGIC)) == MAGIC
	except OSError:
		return False
