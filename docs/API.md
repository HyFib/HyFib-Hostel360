# API reference

HTTP endpoints of HyFib Hostel360 as implemented: portal sign-in, the student and parent
portal, payment gateway callbacks, operations and health, and certificate verification. The
contract summary is in [SOLUTION-DESIGN.md §6](SOLUTION-DESIGN.md#6-portal-http-api); this
page gives the shapes and the rules a client must follow.

## 1. Conventions

- **URL**: `POST https://<site>/api/method/<dotted.path>` (reads also accept `GET`).
  Arguments are form fields or a JSON body; lists may be sent as JSON arrays or JSON strings.
- **Envelope**: success is `{"message": <result>}`. A failure has an HTTP status and
  `{"exc_type": "<ExceptionClass>", "_server_messages": "[...]"}`; the messages are
  translated into the user's language and safe to show. Tracebacks are never returned.

  | Status | `exc_type` | Meaning |
  |---|---|---|
  | 401 | `AuthenticationError` | Not signed in, wrong or expired sign-in code, bad ingest signature or clock |
  | 403 | `PermissionError` and the portal refusals below | Not allowed |
  | 404 | `DoesNotExistError` | Record not found (or not yours) |
  | 409 | `ReplayedRequestError` | Ingest message already processed |
  | 413 | `PayloadTooLargeError` | Ingest body over 512 KB |
  | 417 | `ValidationError` | Input or business rule failed |
  | 422 | `IngestPayloadError` | Ingest message will never be accepted |
  | 429 | `RateLimitExceededError` | Too many requests |
  | 503 | `IngestUnavailableError`, `AlarmNotRecordedError` | Try again later |

- **Sessions**: portal sessions are Frappe sessions (`sid` cookie, `HttpOnly; Secure;
  SameSite=Lax`, 8 hours). Every state-changing call must send `X-Frappe-CSRF-Token` (from
  the page's `csrf-token` meta tag or `get_csrf_token`).
- **Language**: send `_lang=en` or `_lang=ta` (the portal does on every call); signed-in
  users' messages follow `User.language`, set by `set_language`.
- **Dates**: `YYYY-MM-DD`; date-times `YYYY-MM-DD HH:MM:SS` in Asia/Kolkata.
- **Money**: rupees as numbers with two decimals (gateway checkout amounts are in paise).

## 2. Sign-in — `hostel360.api.auth`

### `send_otp` (guest, POST)

Request: `mobile` (10-digit Indian number, any common format), `channel` (`sms` | `whatsapp`,
optional; must be allowed by Messaging Settings).

```json
{"message": {"sent": true, "masked": "98xxxxxx10", "resend_after": 60, "expires_in": 300}}
```

The response is the same for unknown, unvalidated and closed numbers. Limits: one code per
number every 60 s, 5 an hour; 20 an hour per address (trusted networks exempt); a flood guard
of 3,000 calls an hour per address (100,000 for trusted networks). A refused request is 429.

### `verify_otp` (guest, POST)

Request: `mobile`, `otp`, `kind` (`student` | `parent`, only when asked to choose).

```json
{"message": {"ok": true, "kind": "student", "redirect": "/hostel"}}
```

sets the session cookie. When the number belongs to both a student and a guardian the code is
not consumed and the client repeats the call with `kind`:

```json
{"message": {"ok": false, "choose": ["student", "parent"]}}
```

A wrong or expired code is a 401 (`AuthenticationError`) whose message says how many attempts
are left; 5 wrong attempts invalidate the code and 10 failed verifications in an hour lock the
number for an hour (429). A malformed code is a 417.

### `get_csrf_token` (signed in)

`{"message": {"csrf_token": "…"}}`

### `unlock_sign_in` (desk: Hostel Administrator, Hostel Warden, Hostel Office Staff, System Manager; POST)

Request: `mobile`. Lifts the one-hour lock. `{"message": {"ok": true}}`

## 3. Student and parent portal — `hostel360.api.portal`

Every call except `get_privacy_notice` requires a signed-in Hostel Student or Hostel Parent.
For parents, `student` selects one of their wards (default: the first); for students it is
ignored. Order of checks on every call: sign-in rule → privacy consent (except session,
privacy notice and language) → ward → validation and Active status (fees, receipts and
profile stay open to a validated student who is not Active) → the module's own rules.

Portal refusals (HTTP 403, `exc_type`):

| `exc_type` | When | What the portal does |
|---|---|---|
| `PortalAccessClosed` | The record may not use the portal now (not validated, rejected, vacated/alumni, unlinked, portal switched off) | Signs out and shows `/access-closed` with the reason and the office contacts |
| `PortalConsentRequired` | The current privacy notice has not been accepted | Shows `/privacy` |
| `PortalValidationPending` | The student's registration is not validated | Explains the wait |
| `PortalStudentInactive` | Validated but not Active (for example Suspended) | Shows dues only |

Write limits per signed-in user (requests per window):

| Action | Limit | Action | Limit |
|---|---|---|---|
| `accept_privacy_notice` | 20 / hour | `apply_leave` | 20 / hour |
| `set_language` | 30 / hour | `cancel_leave` | 30 / hour |
| `confirm_profile` | 20 / hour | `decide_leave` | 60 / hour |
| `create_payment` | 30 / hour | `raise_grievance` | 10 / hour |
| `verify_payment` | 60 / hour | `comment_grievance` | 60 / hour |
| `get_payment_status` | 120 / hour | `reopen_grievance` | 10 / hour |
| PDF and attachment downloads | 60 / 10 min | `rate_grievance` | 20 / hour |
| `download_photo` | 300 / 10 min | `upload_attachment` | 30 / hour |
| `apply_mess_reduction` | 20 / hour | `submit_feedback` | 20 / hour |
| `cancel_mess_reduction` | 30 / hour | `request_bonafide` | 10 / hour |
| `mark_notifications_read` | 120 / hour | | |

### 3.1 Session, privacy and language

**`get_session`** → who is signed in and what the app needs at start-up:

```json
{
  "user": "s.arun@portal.hostel360.invalid",
  "kind": "student",
  "display_name": "Arun Kumar",
  "mobile_masked": "98xxxxxx10",
  "wards": [{
    "student": "21CS045", "full_name": "Arun Kumar", "register_number": "21CS045",
    "photo": "/api/method/hostel360.api.portal.download_photo?student=21CS045",
    "hostel": "BH1", "hostel_name": "Boys Hostel I", "room": "BH1-214", "room_number": "214",
    "bed": "BH1-214-B", "bed_label": "B", "status": "Active", "validation_status": "Validated",
    "payment_enabled": true
  }],
  "validation_status": "Validated",
  "profile_confirmed": true,
  "consent_required": false,
  "privacy_notice_version": "1.0",
  "privacy_consent_version": "1.0",
  "unread_notifications": 2,
  "language": "ta",
  "settings": {
    "institution_name": "…", "institution_short_name": "ACGCET", "logo": "/files/logo.png",
    "office_phone": "…", "office_email": "…", "support_phone": "+91 96060 60596", "support_email": "support@hyfib.com",
    "payment_enabled": true, "mess_reduction_min_days": 3, "mess_day_cutoff_hour": 12,
    "leave_requires_parent_approval": true, "time_zone": "Asia/Kolkata",
    "upload": {"max_bytes": 5242880, "extensions": ["jpeg", "jpg", "pdf", "png", "webp"]}
  }
}
```

**`get_privacy_notice`** (guest allowed; 300 a minute per address) →
`{version, notice (sanitised HTML), accepted_version, accepted_on, consent_required}`
(the last three are empty for guests).

**`accept_privacy_notice`** (`version`) → `{ok: true}`.

**`set_language`** (`lang`: `en` | `ta`) → `{ok: true, language}`.

### 3.2 Home and profile

**`get_dashboard`** (`student?`):

```json
{
  "student": "21CS045", "student_name": "Arun Kumar", "student_status": "Active", "restricted": false,
  "room": {"allotment": "ALT-2026-00012", "hostel": "BH1", "hostel_name": "…", "block": "BH1-A", "block_name": "…",
           "floor_label": "First Floor", "room": "BH1-214", "room_number": "214", "room_type": "Double",
           "bed": "BH1-214-B", "bed_label": "B", "from_date": "2026-07-01", "to_date": null},
  "fees": {"outstanding": 12500.0, "overdue": 0.0, "overdue_count": 0, "next_due_date": "2026-11-10", "payment_enabled": true},
  "today_menu": {"mess": "Main Mess", "date": "2026-10-15", "day": "Thursday",
                 "meals": [{"meal": "Breakfast", "dishes": "Idli, sambar", "is_special": false}]},
  "pending": {"leaves": 0, "reductions": 1, "grievances": 1, "certificates": 0},
  "notifications": [{"name": "…", "subject": "…", "message": "…", "created": "…", "read": false, "link": "/hostel/fees", "student": "21CS045"}],
  "unread_notifications": 2,
  "unavailable": []
}
```

`fees` is `null` and the section name is in `unavailable` when that part failed to load (the
rest still loads). For a student who is not Active, `restricted` is true and `today_menu`
and `pending` are empty. Parents also get `pending.approvals`.

**`get_profile`** (`student?`) → profile: `name, register_number, roll_number, first_name,
last_name, full_name, gender, date_of_birth, blood_group, photo, program, department,
year_of_study, admission_year, academic_year, mobile_masked, email, address_line1,
address_line2, city, district, state, pincode, emergency_contact_name,
emergency_contact_phone, status, validation_status, profile_confirmed,
privacy_consent_version, mess_enrolled, room {…as above}, guardians [{guardian,
guardian_name, relation, mobile_masked, is_primary, can_approve_leave}], editable_fields,
can_edit`.

**`confirm_profile`** (student only; `updates`: object with any of `email, address_line1,
address_line2, city, district, state, pincode, emergency_contact_name,
emergency_contact_phone, blood_group`) → `{ok: true}`; sets `profile_confirmed`.

**`download_photo`** (GET, `student?`) → the image.

### 3.3 Fees and payments

**`get_fees`** (`student?`):

```json
{
  "student": "21CS045", "outstanding": 12500.0, "overdue": 0.0, "overdue_count": 0, "next_due_date": "2026-11-10",
  "invoices": [{"name": "INV-2026-00031", "invoice_type": "Hostel Fee", "academic_year": "2026-2027",
                "posting_date": "2026-10-01", "due_date": "2026-11-10", "total_amount": 25000.0, "paid_amount": 12500.0,
                "outstanding_amount": 12500.0, "status": "Partly Paid", "payment_in_progress": false,
                "items": [{"fee_head": "Hostel Rent", "description": null, "amount": 20000.0}]}],
  "payments": [{"name": "RCPT-2026-00007", "payment_date": "2026-10-03", "amount": 12500.0,
                "mode_of_payment": "Online", "reference_no": "pay_Q1…"}],
  "payment_enabled": true,
  "payment_test_mode": false
}
```

**`create_payment`** (`invoices`: up to 20 invoice names; `student?`) → starts an online
payment. Asking again for the same invoices while a payment is open returns the same order.

```json
{"transaction": "PTX-2026-000014", "gateway": "razorpay", "amount": 12500.0, "test_mode": 0,
 "checkout": {"key": "rzp_live_…", "order_id": "order_…", "amount": 1250000, "currency": "INR",
              "name": "ACGCET Hostels", "description": "Hostel dues: INV-2026-00031",
              "prefill": {"name": "Arun Kumar", "email": "", "contact": "+919876543210"},
              "notes": {"transaction": "PTX-2026-000014", "student": "21CS045"}, "timeout": 3540}}
```

For PayU, `gateway` is `payu` and the client posts a top-level HTML form:

```json
{"checkout": {"action_url": "https://secure.payu.in/_payment", "method": "POST",
              "fields": {"key": "…", "txnid": "…", "amount": "12500.00", "productinfo": "Hostel dues PTX-…",
                         "firstname": "Arun", "email": "…", "phone": "9876543210",
                         "surl": "https://<site>/api/method/hostel360.api.payments.payu_return",
                         "furl": "https://<site>/api/method/hostel360.api.payments.payu_return",
                         "udf1": "PTX-2026-000014", "hash": "…"}}}
```

**`verify_payment`** (Razorpay; `transaction`, `razorpay_payment_id`, `razorpay_order_id`,
`razorpay_signature`) and **`get_payment_status`** (`transaction`; settles an open PayU
payment with the gateway first) return:

```json
{"status": "Success", "payment": "RCPT-2026-00008", "receipt_number": "RCPT-2026-00008",
 "transaction": "PTX-2026-000014", "test_mode": 0}
```

`status` is `Success`, `Pending` (with `message`; the receipt follows from the webhook or
reconciliation), `Failed` (this attempt failed; the dues stay payable and paying again reuses
the order), `Expired` or `Refunded` (with `message`).

**`download_receipt`** (`payment`) and **`download_fee_structure`** (`student?`) → PDF.

### 3.4 Mess

**`get_menu`** (`week_start?`, `student?`) → `{mess, week_start, week_end, status, published,
notes, days: [{date, day, is_today, meals: [{meal, dishes, is_special}]}]}` (only published
menus; `days` empty otherwise).

**`get_mess_bills`** (`student?`) → `[{name, mess, from_date, to_date, enrolled_days,
reduction_days, chargeable_days, daily_rate, mess_charge, extras_amount, rounding_adjustment,
total_amount, fee_invoice, invoice_status, outstanding_amount, due_date}]`.

**`list_mess_reductions`** (`student?`) → `[{name, from_date, to_date, number_of_days, reason,
description, status, approval_remarks, decided_on, home_visit_leave, applied_on, can_cancel}]`.

**`apply_mess_reduction`** (`from_date`, `to_date`, `reason` — one of Home Visit, Medical,
Internship, Sports or Cultural Event, College Tour, Other — `description?`, `student?`) →
`{name}`. Rules: at least `mess_reduction_min_days` days, advance notice, no overlap, monthly
cap.

**`cancel_mess_reduction`** (`name`) → `{ok: true}` (pending applications only).

### 3.5 Leave

**`list_leaves`** (`student?`) → `[{name, from_datetime, to_datetime, number_of_days,
destination, reason, contact_during_leave, applied_by, status, parent_approval_status,
parent_remarks, warden_approval_status, warden_remarks, apply_mess_reduction,
mess_reduction_days, mess_reduction, checked_out_on, returned_on, creation, can_cancel}]`.

**`apply_leave`** (`from_datetime`, `to_datetime`, `destination`, `reason`,
`contact_during_leave?`, `apply_mess_reduction?`, `student?`) → `{name, status}`. A parent
applying skips the parent step (`Pending Warden Approval`).

**`cancel_leave`** (`name`) → `{ok: true, status}`; pending and approved leaves (an approved
leave's reduction is withdrawn).

**`pending_leave_approvals`** (parent) → `[{name, student, student_name, from_datetime,
to_datetime, number_of_days, destination, reason, contact_during_leave,
apply_mess_reduction, creation}]`.

**`decide_leave`** (parent; `name`, `action` = `approve` | `reject`, `remarks?`) →
`{ok: true, status}`.

### 3.6 Grievances and attachments

**`list_grievances`** (`student?`) → `[{name, subject, category, priority, status,
is_confidential, creation, modified, resolved_on, student_rating}]`; a parent does not see a
confidential grievance they did not raise.

**`get_grievance`** (`name`):

```json
{"name": "GRV-2026-00042", "student": "21CS045", "subject": "Fan not working", "category": "Electrical",
 "description": "…", "priority": "Medium", "is_confidential": 0, "status": "In Progress",
 "creation": "…", "sla_due_by": "…", "assigned": true, "resolution": null, "resolved_on": null,
 "student_rating": null, "student_feedback": null, "can_comment": true, "can_reopen": false, "can_rate": false,
 "attachments": [{"name": "…", "file_name": "fan.jpg", "file_size": 183402,
                  "url": "/api/method/hostel360.api.portal.download_grievance_attachment?name=GRV-2026-00042&file=…"}],
 "timeline": [{"id": "…", "on": "…", "by": "Hostel Office", "is_staff": true, "message": "Grievance registered.",
               "display_message": "…", "status_change": null, "is_mine": false}]}
```

Internal staff notes are never included.

**`raise_grievance`** (`category`, `subject`, `description`, `priority?` (default Medium),
`is_confidential?`, `attachments?` — up to 5 `file_url`s from `upload_attachment`,
`student?`) → `{name}`. *Ragging or Harassment* is always confidential and urgent.

**`comment_grievance`** (`name`, `comment`), **`reopen_grievance`** (`name`, `reason`; within
7 days of resolution), **`rate_grievance`** (`name`, `rating` 1–5, `feedback?`) → `{ok: true}`.

**`upload_attachment`** (multipart `file`, `purpose` = `grievance`, `student?`) →
`{file_url, file_name, file_size}`. PDF, JPG, JPEG, PNG or WEBP up to 5 MB; content must match
the extension; stored privately; at most 20 unattached uploads a day; removed after 24 hours if
never attached; scanned by ClamAV.

**`download_grievance_attachment`** (GET; `name`, `file`) → the file.

### 3.7 Feedback, certificates, notifications

**`submit_feedback`** (student only; `feedback_type` — Mess, Hostel, Facilities, General —
`overall` 1–5, `food_quality?`, `cleanliness?`, `staff_behaviour?`, `facilities?`,
`comments?`, `is_anonymous?`, `period?` `YYYY-MM`, default this month) → `{name}`; one per type
and month.

**`list_certificates`** (`student?`) → `[{name, purpose, purpose_details, status, creation,
approved_on, rejection_reason, certificate_number, issue_date, valid_till, can_download,
is_expired}]`.

**`request_bonafide`** (`purpose` — Bank Loan, Scholarship, Passport, Visa, Internship,
Address Proof, Other — `purpose_details?`, `student?`) → `{name}`.

**`download_bonafide`** (`name`) → PDF (approved or issued only).

**`list_notifications`** (`limit?` 1–100, default 20; `start?`) → `[{name, subject, message,
created, read, link, student}]` (`link` only when it is a portal route).

**`mark_notifications_read`** (`names?`; all when omitted) → `{ok: true}`.

## 4. Payment gateway callbacks — `hostel360.api.payments`

| Endpoint | Caller | Verification | Responses |
|---|---|---|---|
| `razorpay_webhook` (guest, POST, 600/min) | Razorpay | `X-Razorpay-Signature` = HMAC-SHA256 of the raw body with the webhook secret | `200 {"status": …}` processed or ignored (unknown order); `400 {"status":"invalid_signature"\|"not_configured"\|"invalid_payload"}`; `500 {"status":"error"}` (Razorpay retries) |
| `payu_return` (guest, POST, 30/min) | the payer's browser after PayU | PayU reverse hash with the merchant salt, then PayU's verify API | `303` to `/hostel/fees?payment=<transaction>&status=success\|failed\|pending` |
| `payu_webhook` (guest, POST, 600/min) | PayU | as `payu_return` | `200` processed or ignored; `400` invalid signature or not configured; `503 {"status":"retry"}` |

Razorpay events handled: `payment.captured`, `order.paid` (receipt), `payment.failed`
(attempt recorded; the transaction stays Pending until it expires), `payment.authorized`
(pending; reconciliation captures), `refund.created`, `refund.processed`. Configure the
webhook with `payment.captured`, `payment.failed`, `order.paid` and `refund.processed`. All
handling is idempotent; the receipt is issued once whichever path arrives first.

## 5. Health — `hostel360.api.health`

| Endpoint | Auth | Response |
|---|---|---|
| `liveness` (GET/POST, 600/min) | guest | `{"status": "ok"}`; no database access (load balancer health check) |
| `readiness` (GET/POST, 300/min) | guest | `{"status": "ok"}` or HTTP 503 `{"status": "degraded"}` (database or Redis down) |
| `status` (GET/POST, 60/min) | HyFib Operations, Institution Auditor, System Manager | see below |

`status` is `ok`, `warning` or `critical` (the worst component).

```json
{"status": "warning", "instance_label": "ACGCET Hostel360", "environment": "Production",
 "checked_at": "…", "last_health_check_on": "…", "last_heartbeat_sent_on": "…",
 "components": [{"key": "health.database.latency", "label": "Database", "category": "Performance",
                 "status": "ok", "message": "Query round trip 3 ms (warning above 500 ms).",
                 "metrics": [{"metric": "db_latency_ms", "value": 3.1, "unit": "ms"}], "core": true}],
 "open_alerts": {"Info": 0, "Warning": 1, "Critical": 0}, "last_backup": {"name": "BKP-2026-00031", "backup_type": "Daily", "finished_at": "…", "size_display": "1.2 GB"},
 "versions": {"frappe": "16.52.0", "hostel360": "1.0.0"}}
```

## 6. Operations — `hostel360.api.ops`

### `ingest` (guest, POST, 600/min; HMAC)

Headers: `X-H360-Timestamp: <unix seconds>` (within ±300 s of server time) and
`X-H360-Signature: <hex HMAC-SHA256(ingest_secret, "<timestamp>.<raw body>")>` (a `sha256=`
prefix is accepted). Body: a JSON object up to 512 KB. Every message should carry a
`message_id` (8–100 characters of `A–Z a–z 0–9 . _ : -`) created once and kept on every retry.

| `type` | Fields | Response |
|---|---|---|
| `alert` | `key` (required), `title`, `severity` (Info/Warning/Critical), `category`, `source` (Host Agent/Uptime Probe/Security), `description`, `details`, `is_outage`, `host` | `{ok: true, alert: "<Ops Alert>"}` |
| `resolve` | `key` (required), `notes`, `source` | `{ok: true, resolved: true\|false}` |
| `metrics` | `samples` (1–500): `[{metric, value, unit?, recorded_at?, host?}]`, `source`, `host` | `{ok: true, recorded, rejected}` |
| `backup` | `backup_type` (Daily/Weekly/Monthly/Restore Test/Snapshot), `status` (Success/Failed), `started_at`, `finished_at`, `size_bytes`, `snapshot_id`, `repository`, `host`, `message`, `source` | `{ok: true, record: "<Ops Backup Record>"}` |
| `heartbeat` | `host` | `{ok: true}`; records `agent_heartbeat` and resolves `agent.heartbeat.<host>` |

Agent behaviour by status: **2xx** delivered; **409** an earlier attempt of the same message
(same signature or same `message_id`) was committed — delivered; **400, 413, 422** permanent —
drop; **401** signature or clock — fix the configuration; **503 and other 5xx** — keep in the
spool and retry. Twenty bad signatures in 15 minutes raise `security.ingest.invalid_signatures`.

Example (bash):

```bash
body='{"type":"heartbeat","host":"hostel360-app","message_id":"'"$(uuidgen)"'"}'
ts=$(date +%s)
sig=$(printf '%s.%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$H360_INGEST_SECRET" -hex | awk '{print $NF}')
curl -sS -X POST "https://<site>/api/method/hostel360.api.ops.ingest" \
     -H "Content-Type: application/json" -H "X-H360-Timestamp: $ts" -H "X-H360-Signature: $sig" -d "$body"
```

### `oci_notification` (guest, POST, 300/min)

Subscription URL: `https://<site>/api/method/hostel360.api.ops.oci_notification?token=<oci_notification_token>`
(add `&outage=1` for alarms that mean an outage). The confirmation handshake
(`X-OCI-NS-ConfirmationURL`, only to `*.oraclecloud.com`) is answered automatically. An alarm
becomes the Ops Alert `oci.alarm.<16 hex>`: FIRING/REPEAT raise it, OK/RESET resolve it.
Response `{ok: true, actions: [{key, action: "raised"|"resolved"|"ignored"|"failed", alert?}]}`;
**503** when an alarm could not be recorded, so OCI delivers it again.

### Alert lifecycle (HyFib Operations or System Manager)

| Endpoint | Request | Response |
|---|---|---|
| `acknowledge` (POST) | `name` | `{ok: true, status}` |
| `resolve` (POST) | `name`, `notes?` | `{ok: true, status}` |
| `suppress` (POST) | `name`, `notes?` | `{ok: true, status}` |
| `summary` (GET/POST) | – | `{open: {"Info": n, "Warning": n, "Critical": n}}` |

## 7. Certificate verification (public)

- Page: `GET /verify-certificate?code=<16-character code>` (from the QR code on the
  certificate).
- JSON: `hostel360.student_services.verification.verify` (guest, GET/POST, 30 per 10 minutes
  per address), `code` →

```json
{"state": "valid", "certificate_number": "BC/2026/00042", "student_name": "A**** K****",
 "program": "B.E. Computer Science and Engineering", "purpose": "Bank Loan",
 "issue_date": "15-10-2026", "valid_till": "13-01-2027",
 "message": "This certificate is genuine and valid."}
```

`state` is `valid`, `expired` or `not_found`; only what is needed to match the paper is
disclosed.
