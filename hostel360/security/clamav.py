"""Minimal clamd client speaking the INSTREAM protocol over TCP.

Protocol: send ``zINSTREAM\\0``, then the data in chunks each prefixed with its length as
a 4-byte big-endian integer, then a zero-length chunk. clamd answers ``stream: OK``,
``stream: <signature> FOUND`` or an ``... ERROR`` line.
"""

import socket
import struct
from dataclasses import dataclass

CHUNK_SIZE = 64 * 1024
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 60


class ScannerUnavailable(Exception):
	"""clamd could not be reached or did not answer properly."""


@dataclass
class ScanResult:
	clean: bool
	signature: str | None = None
	raw: str = ""


def scan_bytes(data: bytes, host: str, port: int, timeout: int = READ_TIMEOUT) -> ScanResult:
	try:
		with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT) as conn:
			conn.settimeout(timeout)
			conn.sendall(b"zINSTREAM\0")
			view = memoryview(data)
			for offset in range(0, len(data), CHUNK_SIZE):
				chunk = view[offset : offset + CHUNK_SIZE]
				conn.sendall(struct.pack("!L", len(chunk)) + chunk.tobytes())
			conn.sendall(struct.pack("!L", 0))
			reply = _read_reply(conn)
	except OSError as e:
		raise ScannerUnavailable(f"{type(e).__name__}: {e}") from e
	return parse_reply(reply)


def _read_reply(conn) -> str:
	parts = []
	while True:
		block = conn.recv(4096)
		if not block:
			break
		parts.append(block)
		if b"\0" in block or b"\n" in block:
			break
	return b"".join(parts).replace(b"\0", b"").decode("utf-8", "replace").strip()


def parse_reply(reply: str) -> ScanResult:
	if not reply:
		raise ScannerUnavailable("clamd closed the connection without a result")
	body = reply.split(":", 1)[1].strip() if ":" in reply else reply
	if body == "OK":
		return ScanResult(clean=True, raw=reply)
	if body.endswith("FOUND"):
		return ScanResult(clean=False, signature=body[: -len("FOUND")].strip() or "Unknown", raw=reply)
	raise ScannerUnavailable(f"clamd error: {reply[:200]}")


def ping(host: str, port: int) -> bool:
	try:
		with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT) as conn:
			conn.settimeout(CONNECT_TIMEOUT)
			conn.sendall(b"zPING\0")
			return _read_reply(conn) == "PONG"
	except OSError:
		return False
