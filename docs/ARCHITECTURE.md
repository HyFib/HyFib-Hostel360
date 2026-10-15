# Architecture

This document explains how HyFib Hostel360 is put together and why. The contract-level
detail (every DocType, field, permission and function signature) is in
[SOLUTION-DESIGN.md](SOLUTION-DESIGN.md); the production build and run instructions are in
`deploy/README.md`.

## 1. Components

```mermaid
flowchart LR
    subgraph Users
        S["Students and parents<br/>phone browser, PWA"]
        W["Wardens, office, mess,<br/>security staff, auditors"]
        H["HyFib operations"]
    end

    subgraph OCI_Hyd["OCI Hyderabad (ap-hyderabad-1)"]
        LB["OCI Flexible Load Balancer<br/>TLS 1.2/1.3, 80→443"]
        subgraph VM["VM.Standard.A1.Flex 8 OCPU / 16 GB, private subnet"]
            WAF["waf<br/>nginx + ModSecurity + OWASP CRS 4"]
            FE["frontend<br/>Frappe nginx, static assets"]
            BE["backend<br/>gunicorn, Frappe v16 + hostel360"]
            WS["websocket<br/>socket.io"]
            QS["queue-short ×2<br/>alerts, short, default"]
            QL["queue-long<br/>alerts, long, default, short"]
            SCH["scheduler"]
            DB[("MariaDB 11.8")]
            RC[("Redis cache")]
            RQ[("Redis queue")]
            AV["ClamAV clamd"]
            PHP["PHP-FPM<br/>website integration"]
            AG["host scripts<br/>h360-agent, h360-backup,<br/>h360-uptime-probe, maldet"]
        end
        MON["OCI Monitoring alarms<br/>Notifications topic"]
    end

    subgraph OCI_Mum["OCI Mumbai (ap-mumbai-1)"]
        OBJ[("Object Storage<br/>restic repository, versioned")]
        KUMA["Uptime Kuma<br/>external probe + ingest relay"]
    end

    subgraph Ext["External services"]
        PG["Razorpay / PayU"]
        MSG["SMS gateway (DLT),<br/>WhatsApp Cloud API, SMTP"]
    end

    S --> LB
    W --> LB
    H --> LB
    LB --> WAF
    WAF --> FE
    WAF --> BE
    WAF --> WS
    WAF --> PHP
    BE --> DB
    BE --> RC
    BE --> RQ
    QS --> RQ
    QL --> RQ
    SCH --> RQ
    QS --> DB
    QL --> DB
    BE --> AV
    QS --> AV
    BE --> PG
    QL --> PG
    QS --> MSG
    BE --> MSG
    AG -->|"HMAC-signed ingest"| WAF
    AG -->|"restic AES-256"| OBJ
    MON -->|"alarm webhook"| LB
    KUMA -->|"probe and uptime_external samples"| LB
```

| Component | Responsibility | Code |
|---|---|---|
| Frappe desk | Staff UI: workspaces per area, forms with custom actions, reports, desk pages (Room Board, Approvals, Gate Pass, Operations Status) | `hostel360/*/workspace`, `*/page`, `*/report`, `*.js` beside each DocType |
| Portal SPA | Student and parent UI at `/hostel` (Vue 3, PWA), talking only to `hostel360.api.portal` and `hostel360.api.auth` | `frontend/`, `hostel360/www/hostel.py` |
| Portal API | The only path from a portal user to data: identity, consent and ownership checks, then the module service | `hostel360/api/portal.py`, `hostel360/utils/context.py` |
| Module services | Business rules shared by the portal, desk and other modules | `hostel360/<module>/services.py` and DocType controllers |
| Gateways | Razorpay and PayU adapters behind one `PaymentGateway` interface | `hostel360/hostel_fees/gateways/` |
| Messaging | Template rendering (sandboxed Jinja), SMS/WhatsApp/e-mail providers with circuit breakers, Message Log, retries | `hostel360/hostel_communication/` |
| Operations | Health checks, alerts, escalation, channels, support SLAs, monthly report, ingest | `hostel360/hostel_operations/`, `hostel360/api/ops.py`, `hostel360/api/health.py` |
| Security | Upload policy, malware scanning, sharing rules, security event log | `hostel360/security/` |
| Migration | Legacy migration, exit export, verification, purge and certificate | `hostel360/hostel_migration/`, `hostel360/commands/` |
| Infrastructure | Image, Compose stack, WAF, Terraform, Ansible, host scripts, monitoring | `deploy/` |

### Modules and their dependencies

```mermaid
flowchart TB
    SETUP["Hostel Setup<br/>hostels, rooms, beds, settings"]
    PEOPLE["Hostel People<br/>students, guardians, staff, allotments"]
    FEES["Hostel Fees<br/>structures, invoices, receipts, gateways"]
    MESS["Hostel Mess<br/>stock, menu, reductions, billing"]
    SS["Student Services<br/>leave, grievances, bonafide, feedback"]
    COMM["Hostel Communication<br/>notify, templates, OTP delivery"]
    OPS["Hostel Operations<br/>alerts, health, tickets, reports"]
    MIG["Hostel Migration<br/>migration, export, purge"]

    PEOPLE --> SETUP
    FEES --> PEOPLE
    MESS --> PEOPLE
    MESS --> FEES
    SS --> PEOPLE
    SS --> MESS
    PEOPLE --> COMM
    FEES --> COMM
    MESS --> COMM
    SS --> COMM
    FEES --> OPS
    COMM --> OPS
    MIG --> PEOPLE
    MIG --> FEES
    MIG --> MESS
    MIG --> SS
```

Modules call each other only through the service functions in SOLUTION-DESIGN §5 (imported
lazily, so a module that is not installed yet never breaks another). Mess billing turns its
bills into fee invoices through `hostel_fees.services.create_invoice`; an approved leave asks
`hostel_mess.services.apply_mess_reduction` for the reduction; every module notifies people
through `hostel_communication.dispatch.notify` and reports problems through
`hostel_operations.alerts.raise_alert`.

## 2. Request path through the three security layers

```mermaid
sequenceDiagram
    autonumber
    participant C as Browser or phone
    participant L1 as Layer 1: OCI network<br/>(LB, NSG, DDoS, optional OCI WAF)
    participant L2 as Layer 2: WAF container<br/>(ModSecurity CRS 4, rate limits, headers)
    participant L3 as Layer 3: Frappe + hostel360<br/>(session, CSRF, RBAC, scoping)
    participant D as MariaDB / Redis

    C->>L1: HTTPS to the portal host name
    Note over L1: TLS terminated at the LB, then re-encrypted to :8443.<br/>NSG admits 8443/8080 only from the LB subnet, SSH only from OCI Bastion.
    L1->>L2: request with X-Forwarded-For
    Note over L2: Host must be the portal name (421 otherwise).<br/>CRS paranoia 1, anomaly threshold 5, Frappe exclusions.<br/>Sign-in rate limits (campus egress exempt).<br/>fail2ban bans repeat offenders. CSP, HSTS, nosniff.
    alt blocked
        L2-->>C: 403 or 429
    else allowed
        L2->>L3: proxied to frontend or backend
        Note over L3: before_request blocks portal users at the generic upload endpoints.<br/>Session cookie (HttpOnly, Secure, SameSite=Lax) and X-Frappe-CSRF-Token.<br/>Desk: role permissions + User Permissions on Hostel + row-level hooks.<br/>Portal: identity, sign-in rule, consent and ownership checked on every call.<br/>Uploads: extension, magic bytes and size before any byte is stored, then ClamAV.
        L3->>D: scoped queries
        D-->>L3: rows
        L3-->>C: response (no tracebacks to users)
    end
```

The layers are independent: the WAF does not trust the application to reject bad input,
and the application does not trust the WAF to have caught it. Details and file references
per control are in [SECURITY.md](SECURITY.md).

## 3. Portal sign-in and session rules

```mermaid
sequenceDiagram
    autonumber
    participant P as Portal SPA
    participant A as hostel360.api.auth
    participant O as auth.otp
    participant R as Redis
    participant G as SMS / WhatsApp / SMTP

    P->>A: send_otp(mobile, channel)
    A->>O: request_code
    O->>O: number known, validated, not closed?<br/>(e-mail: an address on that record?)
    alt eligible
        O->>R: store HMAC(code), TTL 300 s, counters
        O->>G: send code to the number, or e-mail it straight to the SMTP server<br/>(bounded time, circuit breaker)
    else unknown, not eligible or no e-mail address
        O->>R: store a code nobody receives, same counters
        O->>O: wait about as long as a real send
    end
    A-->>P: {sent, masked, resend_after, expires_in} (same shape either way)
    P->>A: verify_otp(mobile, otp, kind?)
    A->>O: verify_code (attempts reserved before comparing)
    alt number is both a student and a parent
        A-->>P: {ok:false, choose:["student","parent"]}
    else valid
        A->>A: ensure_portal_user, login_as
        A-->>P: {ok:true, kind, redirect:"/hostel"} + session cookie
    end
    Note over P,A: Every later portal call re-checks the sign-in rule (PortalAccessClosed),<br/>privacy consent (PortalConsentRequired) and ownership of the student.
```

## 4. Payments

Collections go straight to ACGCET's merchant account. Hostel360 never trusts the browser:
a receipt is issued only after the payment has been read back from the gateway, and the same
completion path (`hostel_fees.transactions.complete_transaction`, idempotent and locked per
transaction) is used by the checkout callback, the return post, the webhook and
reconciliation, so whichever arrives first issues the one receipt.

```mermaid
sequenceDiagram
    autonumber
    participant P as Portal (fees page)
    participant API as api.portal
    participant F as hostel_fees.services
    participant GW as Gateway (Razorpay / PayU)
    participant WH as api.payments (webhook / return)
    participant REC as Reconciliation (15 min, 23:30)
    participant DB as Invoices, Transaction, Receipt

    P->>API: create_payment(invoices)
    API->>F: create_payment_order (locks invoices, reuses an open order)
    F->>GW: create order
    F->>DB: Hostel Payment Transaction (Initiated)
    API-->>P: {transaction, gateway, amount, test_mode, checkout}

    alt Razorpay (modal)
        P->>GW: Checkout overlay, payer pays
        GW-->>P: payment id, order id, signature
        P->>API: verify_payment(...)
        API->>F: confirm_checkout: verify signature, fetch payment, capture if authorised
    else PayU (redirect)
        P->>GW: top-level form post to secure.payu.in
        GW->>WH: payu_return (payer's browser, no session)
        WH->>F: confirm_gateway_return: verify hash, ask PayU's verify API
        WH-->>P: 303 to /hostel/fees?payment=…&status=…
        P->>API: get_payment_status(transaction)
    end
    F->>DB: complete_transaction: Hostel Payment submitted, invoices updated, Success

    GW-)WH: webhook (razorpay_webhook / payu_webhook), signature or hash verified
    WH->>F: same completion path (idempotent)
    REC->>GW: settle Pending/Initiated transactions, verify recent ones, refunds
    REC->>F: same completion path
    Note over F,DB: Mismatched amounts, extra captures, refunds and bad signatures raise Ops Alerts.
```

Test mode: while Payment Settings has **Test Mode** on, only the designated test payers (and
administrators) can pay online, receipts are marked `is_test` and excluded from collection
figures, and the portal tells the payer that no real money is taken.

## 5. Alerting pipeline

```mermaid
flowchart LR
    subgraph Sources
        HC["Health checks<br/>every 5 min"]
        APP["Application events<br/>payments, messaging,<br/>malware, jobs, tickets"]
        AGT["h360-agent, h360-backup,<br/>h360-deploy, maldet hook"]
        PRB["h360-uptime-probe<br/>every minute"]
        OCI["OCI Monitoring alarms"]
        KMA["Uptime Kuma relay<br/>(Mumbai)"]
    end

    ING["api.ops.ingest<br/>HMAC, ±300 s, message_id,<br/>Ops Ingest Receipt"]
    OCN["api.ops.oci_notification<br/>token + handshake"]
    SPOOL[("host spool<br/>/var/spool/hostel360/ingest")]
    TOPIC["OCI Notifications topic<br/>(fallback for critical)"]

    RA["alerts.raise_alert / resolve_alert<br/>own transaction, per-key lock,<br/>de-duplication by key"]
    AL[("Ops Alert<br/>Open, Acknowledged,<br/>Suppressed, Resolved")]
    ESC["escalation.run_escalations<br/>every minute"]
    Q["alerts queue<br/>(front of short queue if absent)"]
    CH["Channels<br/>e-mail, SMS, WhatsApp,<br/>Slack, Teams, Google Chat, webhook"]
    L1["L1 helpdesk"]
    L2["L2 technical lead<br/>after 120 min"]
    L3["L3 Director<br/>after 180 min or any outage"]
    HB["Heartbeat URL<br/>(dead-man switch)"]

    HC --> RA
    APP --> RA
    AGT --> ING
    PRB --> ING
    KMA --> ING
    AGT -.->|"app unreachable"| SPOOL
    SPOOL -.->|"retry"| ING
    AGT -.->|"critical, app down"| TOPIC
    OCI --> TOPIC
    TOPIC --> OCN
    ING --> RA
    OCN --> RA
    RA --> AL
    AL --> ESC
    ESC --> Q
    Q --> CH
    CH --> L1
    CH --> L2
    CH --> L3
    HC -->|"core checks healthy"| HB
```

- A stable **alert key** identifies a problem (`health.redis.cache`, `backup.daily.failed`,
  `host.disk.data.<host>`). While an alert with that key is open, repeats only bump its
  counters; a key resolved and raised again within 15 minutes re-opens the same alert quietly.
- `raise_alert` writes on a second database connection, so an alert survives the failure of
  the request that raised it and never rolls back the caller's work.
- Escalation follows Ops Alert Settings: L1 at once, L2 after 120 minutes unresolved, L3
  after 180 minutes or immediately for an outage. Critical alerts that nobody acknowledges
  are repeated every `critical_renotify_minutes` (60).
- Deliveries go to the `alerts` job queue, which every worker serves first, so a page never
  waits behind thousands of fee notifications.
- When the application itself is down, host-side alerts wait in the spool (24 hours, 2,000
  messages; uptime samples 35 days) and critical ones go straight to the OCI Notifications
  topic. Uptime Kuma in Mumbai sees VM and region outages that nothing on the VM can.

The full list of alert keys and what to do about each is in
[OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md#4-alert-catalogue).

## 6. Background work

| Queue | Used for |
|---|---|
| `alerts` | Operations alert deliveries only (defined by the configurator; listed first by every worker) |
| `short` | Notifications (`notify` → `deliver`), malware scans, small jobs |
| `default` | Frappe defaults |
| `long` | Publishing dues, mess bill computation and publishing (runs above 250 bills), bulk student import, migration runs |

Scheduled jobs are listed in SOLUTION-DESIGN §9. The scheduler runs in its own container; the
health checks raise `health.scheduler` when minute jobs stop running.

## 7. Data and records

- **Hostel scoping.** Every transactional record carries a `hostel` link copied from the
  student (`current_hostel`, or `last_hostel` once they have left). User Permissions on
  Hostel filter desk lists for hostel-scoped staff; row-level hooks add block, guardian,
  mess, confidentiality and Message Log rules (SOLUTION-DESIGN §3).
- **History is never deleted.** Allotments are transferred or vacated, invoices and receipts
  are cancelled and amended, grievances keep a timeline. Every DocType tracks changes.
- **Money** is stored in rupees with two decimals and crosses the gateway boundary in paise
  as integers.
- **Files** are private unless an administrator publishes them; portal uploads are always
  private and served through the portal API after the same ownership checks.
- **Time** is Asia/Kolkata throughout; the portal shows hostel time even on a phone set to
  another zone.

## 8. Deployment topology

| Environment | Where | Notes |
|---|---|---|
| Production | OCI Hyderabad, one A1 Flex VM, LB, Bastion; backups in Mumbai | `deploy/README.md` |
| Staging | Same shape (required for the load test) | `h360_environment: staging` |
| Development | `deploy/dev/compose.yml` on a workstation | [DEVELOPMENT.md](DEVELOPMENT.md) |

Releases are container images tagged `<version>-<git sha>`, built for arm64 and amd64 and
pushed to OCIR; `h360-deploy <tag>` on the server migrates with a pre-deploy dump and rolls
back automatically on failure. The GitHub workflows that build and deploy them are described
in [DEPLOYMENT.md](DEPLOYMENT.md#3-continuous-integration-and-delivery).

## 9. Design decisions

| Decision | Why |
|---|---|
| Frappe as the platform | Mature role/permission model, audit trail, reports with Excel/PDF export, background jobs and a desk the office can use without custom UI for every list |
| Portal as a separate SPA over a narrow API | Students and parents never get DocType permissions; one module (`api/portal.py`) holds every ownership check, and the phone UI can be fast and bilingual |
| OTP sign-in to the registered mobile | Students and parents already have the number on record; no passwords to forget or share. The code can also go to the e-mail address on record (optional), but the mobile number stays the identifier |
| Same response for unknown numbers | The sign-in page cannot be used to find out who lives in the hostel |
| Alerts in their own transaction | A failing payment webhook must still leave an alert behind; two callers raising the same key must not deadlock each other's work |
| Dividing-system mess billing in the app | The month's cost divided by student-days is the institution's rule; bills become fee invoices so dues, payment and receipts work the same way |
| One VM with Compose | Meets the tendered sizing with headroom, keeps operations simple, and the RTO of 4 hours is met by rebuilding from Terraform and Ansible plus a restore |
| restic to another Indian region | Encrypted, incremental, versioned off-site copies the server itself cannot purge; ACGCET gets read-only access |
