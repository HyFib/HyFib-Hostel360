# Changelog

All notable changes to HyFib Hostel360. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). The version is `__version__` in
`hostel360/__init__.py`; a release is the git tag `vX.Y.Z` and the image
`<version>-<git sha>`.

## [Unreleased]

### Added

- Sign-in codes by e-mail (compliance statement clause 3.2), off until *Allow E-mail Sign-in
  Codes* is ticked in Messaging Settings. Students and parents still sign in with their
  mobile number; the code goes to the e-mail address on their student or guardian record
  (never to a placeholder portal address), straight to the SMTP server with the same short
  time budget as an SMS, and signs in only the records registered with that address. The
  sign-in page offers **E-mail** as a third choice while it is on. Answers, timing and limits
  are the same as for SMS and WhatsApp, including for numbers with no address on record.
  English and Tamil templates `otp_login-Email` and `otp_login-Email-ta`.
- **Gate** workspace (module *Hostel Gate*) for security staff: students on leave now, the
  Gate Pass page and the leave checked out at the moment.

### Changed

- Every desk role opens on its own workspace and the dock offers only the areas of its work:
  *Hostel Admin* for administrators, wardens, office staff and auditors, *Mess* for mess
  staff, *Gate* for security and *HyFib Operations* for HyFib. Hostel360 opens at `/staff`,
  which sends each person home. Mess, gate and HyFib operations staff no longer see the
  administration overview (fees, beds, grievances); records stay reachable as their
  permissions allow. *Get Support* is also on the *Mess* workspace.
- The *Students on Leave Now* card opens the Gate Pass page or the leave list for people who
  have no access to the Students On Leave report.

### Fixed

- Staff saw "Not permitted ... contact your manager" on every desk page behind the WAF: the
  CRS took three ordinary desk requests for attacks (dashboard number cards, the user settings
  saved on every page, and realtime long polling, which also cut live updates off). Each is
  now exempted on its exact path only (rules 10108, 10112, 10121), with regression checks.
- Amounts in the desk showed "$": the regional settings (INR, Indian digit grouping,
  dd-mm-yyyy, Asia/Kolkata) are now also written to the global defaults the desk formats
  with, at set-up and on every migrate.
- Security staff signed in to an empty desk: Hostel360 is now on their apps screen and opens
  on the *Gate* workspace.
- Demonstration site: only a new site can become a demonstration (`h360-demo-site` used to
  accept any site without non-`UAT` students or non-demo staff, so a real guardian or payment
  could be published); the site's origin is recorded, and a demonstration whose snapshot is
  lost is never re-snapshotted from what visitors left on it.
- Demonstration reset: background jobs no longer run while the snapshot loads (Frappe's workers
  ignore maintenance mode); the site's jobs are refused, drained and, after three minutes,
  stopped, and their document locks cleared. A reset or preparation that is interrupted leaves
  the site as it found it.
- Demonstration snapshot: each snapshot is a generation switched in with one rename, so a
  snapshot that fails or is killed leaves the previous one whole, and a damaged one is refused
  before the site is touched. A restore into code whose workspaces, dock or modules changed now
  migrates (only DocType changes did before, so the desk changes above did not reach the demo).

## [1.0.0] — 2026-10-16

First release, for ACGCET Hostels, Karaikudi (tender ACGCET/4903/B1/2026).

### Added

**Hostel Setup**
- Hostels, blocks, floors, rooms and beds with capacity, room types, facilities and live
  occupancy counters; beds created from room capacity.
- Academic years, departments and programmes; institution settings, portal switches, leave,
  grievance and bonafide settings, privacy notice with versioning.
- *Get started with Hostel360* onboarding; role-based desk workspaces with number cards and
  charts.

**Hostel People**
- Student register with photo, guardians, validation against the college register, medical
  notes restricted to wardens and administrators.
- Bulk student import from Excel/CSV with templates, row-by-row validation, background import
  and a rows-to-fix file.
- Staff registration with desk logins by designation, two-factor sign-in, and hostel and
  block assignments; Hostel Security role for gate staff.
- Room allotment, transfer and vacating with history; Room Board desk page; automatic closing
  of ended allotments.
- Hostel and block scoping of desk access, guardian scoping through wards.

**Hostel Fees**
- Fee heads, fee structures and one-click publishing of dues; invoices, counter receipts,
  reminders and overdue tracking.
- Online payment through Razorpay (modal checkout) or PayU (hosted form), settled to the
  institution's merchant account; receipts only after the gateway confirms; webhooks,
  15-minute and daily reconciliation, refund and extra-capture detection; test mode with
  designated test payers.
- Reports: Fee Collection, Fee Dues, Daily Collection Summary, Gateway Reconciliation; print
  formats for invoices, receipts and fee structures.

**Hostel Mess**
- Items, suppliers, stock receipts, issues, adjustments and returns with moving-average
  valuation and a stock ledger.
- Indents with reorder suggestions, menu link, separation of duties and warden threshold.
- Weekly menu planner with publishing; mess reductions with configurable rules and approver.
- Extras and expenses; monthly bill runs on the dividing system, published as fee invoices.
- Reports: Mess Stock Balance, Mess Consumption (daily, monthly, yearly), Mess Cost Summary,
  Mess Reduction Register.

**Student Services**
- Home-visit leave with parent and warden approval, automatic mess reduction, gate check-out
  and return, overdue marking.
- Grievances with SLA and hourly escalation, confidential handling of ragging and harassment,
  timeline with internal notes, assignment, resolution, rating and reopening.
- Bonafide certificates with numbering, validity, QR code and public verification page.
- Monthly feedback, optionally anonymous.
- Approvals inbox and Gate Pass desk pages; leave, grievance, feedback and bonafide reports.

**Student and parent portal**
- Installable Vue 3 web app at `/hostel` in English and Tamil: OTP sign-in by SMS or WhatsApp,
  privacy consent, confirm details, dashboard, fees and online payment, receipts, fee
  structure, menu, mess bills and reductions, leave and parent approvals, grievances with
  attachments, feedback, certificates, notifications.

**Hostel Communication**
- SMS (MSG91 or generic HTTP gateway, DLT), WhatsApp Cloud API, e-mail and in-app
  notifications from editable templates in English and Tamil; message log with retries and
  provider failure alerts.

**Hostel Operations**
- Five-minute health checks, alert de-duplication and escalation matrix (L1 helpdesk, L2
  technical lead, L3 Director), alert channels (e-mail, SMS, WhatsApp, Slack, Teams, Google
  Chat, webhooks), dedicated alerts queue.
- Host agent ingest with HMAC signatures and replay protection; OCI Monitoring alarm intake;
  uptime measurement on the server and from Mumbai.
- Support tickets with 1-hour response and 4-hour resolution SLAs; backup and restore-test
  records; Security Incidents with the six-hour CERT-In clock.
- Monthly Performance Report produced on the 1st and e-mailed by the 6th; Operations Status
  page; Alert History and Uptime and Incidents reports.

**Hostel Migration**
- Legacy data migration from CSV/XLSX with JSON mappings, dry runs, idempotent re-runs and
  reconciliation of counts, money and file checksums.
- Exit export (CSV, Excel, SQL dump, files, manifest; optional AES-256-GCM encryption),
  verification, deletion of institution data with a signed certificate.
- Demonstration and UAT data; non-interactive site setup.

**Infrastructure and operations**
- Multi-architecture application image; Docker Compose production stack with WAF
  (ModSecurity + OWASP CRS 4), ClamAV, PHP-FPM for the website integration.
- Terraform for OCI (network, Bastion, VM, block volume, load balancer, Object Storage, IAM,
  Vault, alarms, logging, optional OCI WAF) and Ansible for server configuration.
- Host scripts for deployment with automatic rollback, backups with restic to Mumbai,
  restore and monthly restore tests, snapshots, malware hooks, two-factor enrolment,
  decommissioning; exit runbook and cloud clean-up.
- k6 load test for 1,500 concurrent users.

### Security
- Two-factor authentication required for every desk role, System Manager and Administrator;
  password policy and lockout; 8-hour sessions.
- OTP codes stored only as HMAC in Redis, with per-number and per-address limits and the same
  response for unknown numbers; portal sign-in only for validated students and their
  parents.
- Upload policy enforced before files are written (type, content, size, private storage) and
  malware scanning of every upload.
- Log retention raised to the CERT-In minimum of 180 days; personal data export and erasure
  for students and guardians.

### Documentation
- Solution design, architecture, API reference, deployment, operations runbook, backup and
  disaster recovery, security, data migration and exit, compliance matrix, load testing,
  training plan, and user manuals for every role with a Tamil quick guide for students and
  parents.
- Continuous integration, security scanning, image build and deployment workflows.

[Unreleased]: https://github.com/HyFib/HyFib-Hostel360/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/HyFib/HyFib-Hostel360/releases/tag/v1.0.0
