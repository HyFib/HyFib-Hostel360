"""Operations API: host agent ingest, OCI Monitoring notifications, alert lifecycle.

``ingest`` is called by the HyFib host agent on the server (metrics every minute, backup
results, alerts from ClamAV / maldet / the WAF, heartbeats). It is a guest endpoint
authenticated by an HMAC signature over the raw request body:

    X-H360-Timestamp: <unix seconds>
    X-H360-Signature: hex(HMAC-SHA256(ingest_secret, "<timestamp>.<raw body>"))

Requests more than 300 seconds away from server time are rejected. Each accepted request
leaves an Ops Ingest Receipt written in the same transaction as the data it carried, so a
repeated request is answered 409 only when the first one was really committed; if the first
attempt failed and was rolled back, the retry is processed normally. A message may carry a
``message_id``; a re-signed retry of the same message is then recognised as well.

Status codes tell the agent what to do with a message: 2xx and 409 mean delivered, 422 and
413 mean the message will never be accepted (drop it), and 503 or any other 5xx mean try
again later (keep it in the spool).

``oci_notification`` receives OCI Notifications deliveries for Monitoring alarms. The
subscription URL carries a shared token; the subscription confirmation handshake is
completed automatically. When an alarm cannot be recorded the endpoint answers 503 so OCI
delivers it again.
"""

import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, now_datetime

from hostel360.hostel_operations.constants import SEVERITIES
from hostel360.setup.roles import OPERATIONS_ROLE

TIMESTAMP_WINDOW_SECONDS = 300
RECEIPT_DOCTYPE = "Ops Ingest Receipt"
MAX_BODY_BYTES = 512 * 1024
INVALID_SIGNATURE_ALERT_THRESHOLD = 20
INVALID_SIGNATURE_BUCKET_SECONDS = 900
INVALID_SIGNATURE_ALERT_KEY = "security.ingest.invalid_signatures"
MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")
AGENT_SOURCES = ("Host Agent", "Uptime Probe", "Security")
BACKUP_TYPES = ("Daily", "Weekly", "Monthly", "Restore Test", "Snapshot")
OCI_CONFIRMATION_HOST_SUFFIXES = (".oraclecloud.com", ".oci.oraclecloud.com")
OCI_SEVERITY = {"CRITICAL": "Critical", "ERROR": "Critical", "WARNING": "Warning", "INFO": "Info"}
OCI_CATEGORY = {
	"oci_computeagent": "Capacity",
	"oci_blockstore": "Capacity",
	"oci_filestorage": "Capacity",
	"oci_lbaas": "Availability",
	"oci_healthchecks": "Availability",
	"oci_objectstorage": "Backup",
	"oci_vcn": "Performance",
	"oci_waf": "Security",
}
OCI_OUTAGE_NAMESPACES = ("oci_lbaas", "oci_healthchecks")


class PayloadTooLargeError(frappe.ValidationError):
	http_status_code = 413


class IngestPayloadError(frappe.ValidationError):
	"""The message itself is wrong and will never be accepted; the agent drops it."""

	http_status_code = 422


class IngestUnavailableError(frappe.ValidationError):
	"""The message is fine but could not be stored right now; the agent keeps it and retries."""

	http_status_code = 503


class AlarmNotRecordedError(frappe.ValidationError):
	"""OCI Notifications redelivers only after a 5xx (or 429) answer or a timeout."""

	http_status_code = 503


class ReplayedRequestError(frappe.AuthenticationError):
	http_status_code = 409


# Host agent ingest --------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=600, seconds=60)
def ingest() -> dict:
	body = _raw_body()
	signature = verify_ingest_signature(
		body,
		frappe.get_request_header("X-H360-Timestamp") or "",
		frappe.get_request_header("X-H360-Signature") or "",
	)
	try:
		payload = json.loads(body or b"{}")
	except ValueError:
		frappe.throw(_("The request body must be JSON."), IngestPayloadError)
	if not isinstance(payload, dict):
		frappe.throw(_("The request body must be a JSON object."), IngestPayloadError)

	claim_receipt(signature, payload)
	try:
		return handle_ingest(payload)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		# Lock trouble is not the message's fault. The whole request (receipt included) is
		# rolled back, so the agent's retry is processed rather than refused as a replay.
		frappe.log_error(title="Hostel360: ingest could not be stored", message=frappe.get_traceback(), defer_insert=True)
		frappe.throw(_("The message could not be stored right now; please retry."), IngestUnavailableError)


def _raw_body() -> bytes:
	request = frappe.request
	length = cint(request.headers.get("Content-Length"))
	if length > MAX_BODY_BYTES:
		frappe.throw(_("The request is too large."), PayloadTooLargeError)
	body = request.get_data(cache=True) or b""
	if len(body) > MAX_BODY_BYTES:
		frappe.throw(_("The request is too large."), PayloadTooLargeError)
	return body


def verify_ingest_signature(body: bytes, timestamp: str, signature: str, now: float | None = None) -> str:
	"""Check the timestamp window and the HMAC; returns the normalised signature."""
	from hostel360.hostel_operations.channels import sign
	from hostel360.hostel_operations.settings import get_settings

	secret = get_settings().get_password("ingest_secret", raise_exception=False)
	if not secret:
		frappe.throw(_("Ingest is not configured on this server."), frappe.AuthenticationError)

	try:
		sent_at = int(str(timestamp).strip())
	except ValueError:
		frappe.throw(_("Missing or invalid X-H360-Timestamp header."), frappe.AuthenticationError)
	now = time.time() if now is None else now
	if abs(now - sent_at) > TIMESTAMP_WINDOW_SECONDS:
		frappe.throw(
			_("The request timestamp is more than {0} seconds from server time. Check the agent clock.").format(
				TIMESTAMP_WINDOW_SECONDS
			),
			frappe.AuthenticationError,
		)

	provided = str(signature or "").strip().lower()
	provided = provided.removeprefix("sha256=")
	expected = sign(secret, str(sent_at), body)
	if not provided or not hmac.compare_digest(expected, provided):
		_note_invalid_signature()
		frappe.throw(_("Invalid request signature."), frappe.AuthenticationError)
	return expected


def claim_receipt(signature: str, payload: dict) -> None:
	"""Record that this request is being processed, or refuse it as already processed.

	The receipt is an ordinary row in the request's transaction. A concurrent duplicate waits
	on the row until the first request finishes: if that one committed, the duplicate gets
	409; if it rolled back, the receipt went with it and the duplicate is processed.
	"""
	keys = ["sig:" + signature]
	message_id = str(payload.get("message_id") or "").strip()
	if message_id:
		if not MESSAGE_ID_RE.match(message_id):
			frappe.throw(_("message_id must be 8 to 100 letters, digits or . _ : -"), IngestPayloadError)
		keys.append("msg:" + message_id)

	now = now_datetime()
	message_type = str(payload.get("type") or "")[:140]
	host = str(payload.get("host") or "")[:140]
	for key in keys:
		try:
			# A plain insert: this runs for every agent message, and the primary key is the
			# whole point (Document.insert would add a version row and hooks for nothing).
			frappe.db.sql(
				f"""insert into `tab{RECEIPT_DOCTYPE}`
					(name, receipt_key, message_type, host, received_at, creation, modified, owner, modified_by)
				values (%(key)s, %(key)s, %(type)s, %(host)s, %(now)s, %(now)s, %(now)s, 'Administrator', 'Administrator')""",
				{"key": key, "type": message_type, "host": host, "now": now},
			)
		except Exception as exc:
			if frappe.db.is_duplicate_entry(exc):
				frappe.throw(_("This request has already been processed."), ReplayedRequestError)
			raise


def _note_invalid_signature() -> None:
	"""Count bad signatures; a burst suggests a misconfigured agent or someone probing.

	The alert is raised once per 15-minute window, by the first bad request at or above the
	threshold. ``raise_alert`` writes it in a transaction of its own, so it survives the
	rollback of this request (which is about to fail with 401).
	"""
	try:
		bucket = _invalid_signature_bucket()
		key = frappe.cache.make_key(f"hostel360:ops:ingest:invalid:{bucket}")
		count = frappe.cache.incr(key)
		frappe.cache.expire(key, 2 * INVALID_SIGNATURE_BUCKET_SECONDS)
		if count < INVALID_SIGNATURE_ALERT_THRESHOLD:
			return
		flag = frappe.cache.make_key(f"hostel360:ops:ingest:invalid:{bucket}:alerted")
		if not frappe.cache.set(flag, 1, nx=True, ex=2 * INVALID_SIGNATURE_BUCKET_SECONDS):
			return
	except Exception:
		return

	from hostel360.hostel_operations.alerts import raise_alert

	name = raise_alert(
		INVALID_SIGNATURE_ALERT_KEY,
		"Repeated invalid signatures on the host agent endpoint",
		"Warning",
		"Security",
		source="Security",
		description=_("{0} requests with an invalid signature in 15 minutes, last from {1}.").format(
			count, getattr(frappe.local, "request_ip", None) or _("unknown address")
		),
	)
	if not name:
		# Let the next bad request in this window try again.
		try:
			frappe.cache.delete(flag)
		except Exception:
			pass


def _invalid_signature_bucket() -> int:
	return int(time.time() // INVALID_SIGNATURE_BUCKET_SECONDS)


def handle_ingest(payload: dict) -> dict:
	handlers = {
		"alert": _ingest_alert,
		"resolve": _ingest_resolve,
		"metrics": _ingest_metrics,
		"backup": _ingest_backup,
		"heartbeat": _ingest_heartbeat,
	}
	kind = str(payload.get("type") or "").lower()
	handler = handlers.get(kind)
	if not handler:
		frappe.throw(_("Unknown message type {0}.").format(frappe.bold(kind or "-")), IngestPayloadError)
	return handler(payload)


def _source(payload: dict) -> str:
	source = payload.get("source")
	return source if source in AGENT_SOURCES else "Host Agent"


def _ingest_alert(payload: dict) -> dict:
	from hostel360.hostel_operations.alerts import raise_alert

	key = str(payload.get("key") or "").strip()
	if not key:
		frappe.throw(_("An alert needs a key."), IngestPayloadError)
	severity = payload.get("severity") if payload.get("severity") in SEVERITIES else "Warning"
	name = raise_alert(
		key,
		payload.get("title") or key,
		severity,
		payload.get("category") or "Other",
		source=_source(payload),
		description=str(payload.get("description") or ""),
		details=payload.get("details"),
		is_outage=bool(payload.get("is_outage")),
	)
	if not name:
		# raise_alert has logged why (typically a lock or deadlock); the message is fine.
		frappe.throw(_("The alert could not be recorded right now; please retry."), IngestUnavailableError)
	return {"ok": True, "alert": name}


def _ingest_resolve(payload: dict) -> dict:
	from hostel360.hostel_operations.alerts import normalise_key, resolve_alert

	key = str(payload.get("key") or "").strip()
	if not key:
		frappe.throw(_("A resolve message needs a key."), IngestPayloadError)
	resolved = resolve_alert(key, notes=str(payload.get("notes") or ""), source=_source(payload))
	if not resolved and frappe.db.exists("Ops Alert", {"open_key": normalise_key(key)}):
		frappe.throw(_("The alert could not be resolved right now; please retry."), IngestUnavailableError)
	return {"ok": True, "resolved": resolved}


def _ingest_metrics(payload: dict) -> dict:
	from hostel360.hostel_operations.metrics import MAX_BATCH, record_metrics

	samples = payload.get("samples")
	if not isinstance(samples, list) or not samples:
		frappe.throw(_("A metrics message needs a non-empty samples list."), IngestPayloadError)
	if len(samples) > MAX_BATCH:
		frappe.throw(_("Send at most {0} samples per request.").format(MAX_BATCH), IngestPayloadError)
	clean = [s for s in samples if isinstance(s, dict)]
	recorded = record_metrics(clean, source=_source(payload), host=str(payload.get("host") or "")[:140] or None)
	return {"ok": True, "recorded": recorded, "rejected": len(samples) - recorded}


def _ingest_backup(payload: dict) -> dict:
	backup_type = payload.get("backup_type")
	status = payload.get("status")
	if backup_type not in BACKUP_TYPES:
		frappe.throw(_("backup_type must be one of {0}.").format(", ".join(BACKUP_TYPES)), IngestPayloadError)
	if status not in ("Success", "Failed"):
		frappe.throw(_("status must be Success or Failed."), IngestPayloadError)
	try:
		size_bytes = max(float(payload.get("size_bytes") or 0), 0)
	except (TypeError, ValueError):
		frappe.throw(_("size_bytes must be a number."), IngestPayloadError)
	doc = frappe.get_doc(
		{
			"doctype": "Ops Backup Record",
			"backup_type": backup_type,
			"status": status,
			"started_at": _agent_time(payload.get("started_at")),
			"finished_at": _agent_time(payload.get("finished_at")) or frappe.utils.now_datetime(),
			"size_bytes": size_bytes,
			"snapshot_id": str(payload.get("snapshot_id") or "")[:140],
			"repository": str(payload.get("repository") or "")[:140],
			"host": str(payload.get("host") or "")[:140],
			"message": str(payload.get("message") or "")[:2000],
			"source": _source(payload),
			"owner": "Administrator",
		}
	)
	doc.insert(ignore_permissions=True)
	return {"ok": True, "record": doc.name}


def _agent_time(value):
	from hostel360.hostel_operations.metrics import _clamp_time

	if not value:
		return None
	return _clamp_time(value, frappe.utils.now_datetime())


def _ingest_heartbeat(payload: dict) -> dict:
	from hostel360.hostel_operations.alerts import resolve_alert
	from hostel360.hostel_operations.metrics import record_metrics

	host = str(payload.get("host") or "").strip()[:140] or "unknown"
	record_metrics([{"metric": "agent_heartbeat", "value": 1}], source=_source(payload), host=host)
	# A failure here is retried by the next heartbeat a minute later; not worth a 503.
	resolve_alert(f"agent.heartbeat.{host}", notes=_("Host agent reporting again."), source="Host Agent")
	return {"ok": True}


# OCI Monitoring -----------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=300, seconds=60)
def oci_notification() -> dict:
	from hostel360.hostel_operations.settings import get_settings

	# Read the token from the query string itself: Frappe merges the JSON body into
	# form_dict, and a body field must never be able to stand in for the token.
	token = frappe.request.args.get("token") or ""
	expected = get_settings().get_password("oci_notification_token", raise_exception=False) or ""
	if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
		frappe.throw(_("Invalid notification token."), frappe.AuthenticationError)

	body = _raw_body()
	try:
		payload = json.loads(body) if body else {}
	except ValueError:
		payload = {}
	if not isinstance(payload, dict):
		payload = {}

	confirmation_url = frappe.get_request_header("X-OCI-NS-ConfirmationURL") or payload.get("ConfirmationURL")
	if confirmation_url:
		return {"ok": True, "confirmed": confirm_oci_subscription(confirmation_url)}

	outage = cint(frappe.request.args.get("outage"))
	return handle_oci_alarm(payload, force_outage=bool(outage))


def confirm_oci_subscription(url: str) -> bool:
	import requests

	parsed = urlparse(str(url))
	host = (parsed.hostname or "").lower()
	# Only ever call back to Oracle: the URL arrives in an unauthenticated-looking request
	# and must not become a way to make this server fetch arbitrary addresses.
	if parsed.scheme != "https" or not host.endswith(OCI_CONFIRMATION_HOST_SUFFIXES):
		frappe.throw(_("The confirmation URL is not an OCI Notifications address."))
	try:
		response = requests.get(url, timeout=5, allow_redirects=False)
	except requests.RequestException as exc:
		frappe.log_error(title="Hostel360: OCI subscription confirmation failed", message=exc.__class__.__name__)
		return False
	confirmed = 200 <= response.status_code < 300
	if not confirmed:
		frappe.log_error(
			title="Hostel360: OCI subscription confirmation failed", message=f"HTTP {response.status_code}"
		)
	return confirmed


def handle_oci_alarm(payload: dict, force_outage: bool = False) -> dict:
	from hostel360.hostel_operations.alerts import normalise_key, raise_alert, resolve_alert

	entries = payload.get("alarmMetaData") or [{}]
	if not isinstance(entries, list):
		entries = [{}]
	message_type = str(payload.get("type") or "").upper()
	actions = []
	failed = False
	for meta in entries[:20]:
		meta = meta if isinstance(meta, dict) else {}
		alarm_id = meta.get("id") or payload.get("dedupeKey") or payload.get("title")
		if not alarm_id:
			continue
		key = "oci.alarm." + hashlib.sha1(str(alarm_id).encode(), usedforsecurity=False).hexdigest()[:16]
		status = str(meta.get("status") or "").upper()
		firing = status == "FIRING" or (not status and message_type in ("OK_TO_FIRING", "REPEAT"))
		cleared = status == "OK" or (not status and message_type in ("FIRING_TO_OK", "RESET"))
		title = payload.get("title") or meta.get("alarmSummary") or _("OCI Monitoring alarm")

		if cleared:
			resolved = resolve_alert(key, notes=_("OCI alarm returned to OK."), source="OCI Monitoring")
			# False is also the answer when there was nothing open; only an alert that is
			# still open means the resolve itself failed. OCI sends nothing more for this
			# alarm, so a lost OK would leave the alert paging people until closed by hand.
			if not resolved and frappe.db.exists("Ops Alert", {"open_key": normalise_key(key)}):
				failed = True
				actions.append({"key": key, "action": "failed"})
			else:
				actions.append({"key": key, "action": "resolved"})
			continue
		if not firing:
			actions.append({"key": key, "action": "ignored"})
			continue

		namespace = str(meta.get("namespace") or "")
		severity = OCI_SEVERITY.get(str(meta.get("severity") or payload.get("severity") or "").upper(), "Warning")
		dimensions = meta.get("dimensions") if isinstance(meta.get("dimensions"), list) else []
		name = raise_alert(
			key,
			title,
			severity,
			OCI_CATEGORY.get(namespace, "Other"),
			source="OCI Monitoring",
			description=str(payload.get("body") or meta.get("alarmSummary") or "")[:5000],
			details={
				"alarm_id": meta.get("id"),
				"namespace": namespace,
				"query": meta.get("query"),
				"metrics_firing": meta.get("totalMetricsFiring"),
				"dimensions": dimensions[:5],
				"alarm_url": meta.get("alarmUrl"),
				"type": message_type,
				"timestamp_ms": payload.get("timestampEpochMillis"),
			},
			is_outage=force_outage or (severity == "Critical" and namespace in OCI_OUTAGE_NAMESPACES),
		)
		if name:
			actions.append({"key": key, "action": "raised", "alert": name})
		else:
			failed = True
			actions.append({"key": key, "action": "failed"})

	if failed:
		# Alarms already recorded in this delivery are safe to see again: a repeated FIRING
		# only counts another occurrence and a repeated OK finds nothing left to resolve.
		frappe.throw(_("The alarm could not be recorded; please retry."), AlarmNotRecordedError)
	return {"ok": True, "actions": actions}


# Alert lifecycle ----------------------------------------------------------


def _require_operations() -> None:
	from hostel360.utils.context import has_any_role

	if not has_any_role({OPERATIONS_ROLE, "System Manager"}):
		frappe.throw(_("Only HyFib Operations can change alerts."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def acknowledge(name: str) -> dict:
	from hostel360.hostel_operations.alerts import acknowledge_alert

	_require_operations()
	acknowledge_alert(name, frappe.session.user)
	return {"ok": True, "status": frappe.db.get_value("Ops Alert", name, "status")}


@frappe.whitelist(methods=["POST"])
def resolve(name: str, notes: str | None = None) -> dict:
	from hostel360.hostel_operations.alerts import resolve_alert_by_name

	_require_operations()
	resolve_alert_by_name(name, notes or "", frappe.session.user)
	return {"ok": True, "status": frappe.db.get_value("Ops Alert", name, "status")}


@frappe.whitelist(methods=["POST"])
def suppress(name: str, notes: str | None = None) -> dict:
	from hostel360.hostel_operations.alerts import suppress_alert

	_require_operations()
	suppress_alert(name, notes or "", frappe.session.user)
	return {"ok": True, "status": frappe.db.get_value("Ops Alert", name, "status")}


@frappe.whitelist(methods=["GET", "POST"])
def summary() -> dict:
	"""Open alert counts for the desk navbar indicator."""
	from hostel360.hostel_operations.alerts import open_alert_counts
	from hostel360.utils.context import has_any_role

	if not has_any_role({OPERATIONS_ROLE, "System Manager"}):
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	return {"open": open_alert_counts()}
