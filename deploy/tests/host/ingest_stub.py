"""Stand-in for hostel360.api.ops.ingest and the portal's liveness URL (deploy/tests/host).

POST /ingest checks the signature like the application (±300 s, hex HMAC-SHA256 of
"<timestamp>.<body>"), remembers committed message_ids and answers 409 for a repeat, and
otherwise answers what the file <dir>/ingest-mode says:

	ok            200, message committed
	commit-500    message committed (once), but 500 returned every time, so the agent
	              spools it and retries later with a new signature
	<status>      that status, nothing committed (503, 417, 422, ...)

Every request is appended to <dir>/requests.jsonl as {status, committed, body}.
GET /liveness answers the status in <dir>/portal-mode (default 200).

	python3 ingest_stub.py <port> <dir> <secret>
"""

import hashlib
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, DIRECTORY, SECRET = int(sys.argv[1]), sys.argv[2], sys.argv[3]
committed_ids = set()


def mode(name, default):
	try:
		with open(os.path.join(DIRECTORY, name)) as handle:
			return handle.read().strip() or default
	except OSError:
		return default


class Handler(BaseHTTPRequestHandler):
	def log_message(self, *args):
		pass

	def answer(self, status, payload=None):
		body = json.dumps(payload or {}).encode()
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		if self.path.startswith("/liveness"):
			status = int(mode("portal-mode", "200"))
			self.answer(status, {"message": {"status": "ok"}})
		else:
			self.answer(404)

	def do_POST(self):
		length = int(self.headers.get("Content-Length") or 0)
		body = self.rfile.read(length)
		timestamp = self.headers.get("X-H360-Timestamp", "")
		signature = self.headers.get("X-H360-Signature", "")
		expected = hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
		committed = False
		try:
			message = json.loads(body)
		except ValueError:
			message = None
		if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > 300 or not hmac.compare_digest(expected, signature):
			status = 401
		elif not isinstance(message, dict):
			status = 422
		elif mode("ingest-mode", "ok") == "commit-500":
			# Committed (once), but the answer never reaches the agent.
			if message.get("message_id") not in committed_ids:
				committed = True
				committed_ids.add(message.get("message_id"))
			status = 500
		elif message.get("message_id") and message["message_id"] in committed_ids:
			status = 409
		else:
			wanted = mode("ingest-mode", "ok")
			if wanted == "ok":
				committed = True
				if message.get("message_id"):
					committed_ids.add(message["message_id"])
				status = 200
			else:
				status = int(wanted)
		with open(os.path.join(DIRECTORY, "requests.jsonl"), "a") as handle:
			handle.write(json.dumps({"status": status, "committed": committed, "body": message}) + "\n")
		self.answer(status, {"message": {"ok": status == 200}})


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
