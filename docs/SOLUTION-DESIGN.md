# HyFib Hostel360 — Solution Design

Document: HYFIB/H360/SDS/1.3 · Tender ref. ACGCET/4903/B1/2026 · Owner: HyFib Solutions Private Limited

This is the authoritative design for HyFib Hostel360 as delivered to ACGCET Hostels. It
fixes the data model, roles, workflows, service interfaces and portal API that every
module is built against. Field names, DocType names, status values and function
signatures written here are contracts: change them only by changing this document.

Revision 1.1 brought the document in line with the delivered implementation; revision 1.2
adds sign-in codes by e-mail; revision 1.3 gives every desk role its own home and dock (see
§11 for the change lists). Companion documents:
[ARCHITECTURE.md](ARCHITECTURE.md), [API.md](API.md), [SECURITY.md](SECURITY.md),
[OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) and [COMPLIANCE-MATRIX.md](COMPLIANCE-MATRIX.md).

---

## 1. Platform and stack

| Layer | Technology |
|---|---|
| Application | Frappe Framework v16 (Python 3.14), custom app `hostel360`; release v16.52.0 pinned in the image and in CI |
| Database / cache | MariaDB 11.8 LTS (≥ 10.6 as tendered), Redis 8 (cache + job queue) |
| Student/parent portal | Vue 3 + Vite SPA served at `/hostel`, installable PWA, English/தமிழ் |
| Staff interface | Frappe Desk (role-based workspaces), 2FA mandatory |
| Web tier | OCI Load Balancer → Nginx + ModSecurity (OWASP CRS 4) → Frappe Nginx → Gunicorn |
| Payment gateways | Razorpay (modal checkout) or PayU (hosted form post); collections settle to ACGCET's merchant account |
| Messaging | SMS (MSG91 or generic HTTP, DLT), WhatsApp Cloud API, e-mail, in-app notifications |
| Malware protection | ClamAV (clamd, scan-on-upload) + Linux Malware Detect (realtime + daily) |
| PHP runtime | PHP-FPM 8.3 pool, `pm.max_children = 300`, on-demand (tender clause 6.5) |
| Hosting | OCI Hyderabad (ap-hyderabad-1), VM.Standard.A1.Flex 8 OCPU / 16 GB, Ubuntu 24.04 LTS arm64 |
| Backups | restic (AES-256, compressed, incremental) → OCI Object Storage Mumbai (ap-mumbai-1) |
| Monitoring | Hostel360 Operations module + OCI Monitoring alarms + on-host uptime probe + Uptime Kuma (Mumbai) |

Deployment unit: one OCI VM running Docker Compose (WAF, frontend, backend, websocket,
two short-queue workers, one long-queue worker, scheduler, configurator, MariaDB, Redis ×2,
ClamAV, PHP-FPM). Workers list a dedicated `alerts` queue first. See
[DEPLOYMENT.md](DEPLOYMENT.md) and `deploy/README.md`.

## 2. Repository layout

```
pyproject.toml, package.json        Frappe app root (bench get-app compatible)
hostel360/                           Python package
  hooks.py                           Integration contract (see §9)
  setup/                             install hooks, roles, boot, app permission
  utils/                             shared helpers: context.py, validation.py, jinja.py
  auth/                              OTP login and session events
  security/                          upload validation, malware scanning, sharing rules, security log
  api/                               whitelisted HTTP endpoints (portal, auth, payments, ops, health)
  hostel_setup/                      module "Hostel Setup"
  hostel_people/                     module "Hostel People"
  hostel_fees/                       module "Hostel Fees" (gateways/ holds the payment adapters)
  hostel_mess/                       module "Hostel Mess"
  student_services/                  module "Student Services"
  hostel_communication/              module "Hostel Communication" (providers/ holds SMS, WhatsApp, e-mail)
  hostel_operations/                 module "Hostel Operations"
  hostel_migration/                  module "Hostel Migration": legacy data migration + exit export (TR 5, TR 22)
  hostel_gate/                       module "Hostel Gate": the Gate workspace of security staff
  commands/                          bench commands (see below)
  www/hostel.html                    SPA entry (written by the frontend build)
  www/staff.py                       /staff: sends each desk user on to their home workspace
  www/verify-certificate.html        public bonafide certificate verification
  locale/                            translation catalogue (main.pot, ta.po)
frontend/                            Vue 3 SPA source
deploy/                              docker, compose, nginx/WAF, php-fpm, terraform, ansible, host scripts,
                                     monitoring, exit runbook, regression checks (deploy/tests)
docs/                                architecture, deployment, operations, security, manuals
tests/load/                          k6 load test for 1,500 concurrent users
.github/workflows/                   CI, security scanning, image build, deploy
```

Bench commands (`hostel360/commands/__init__.py`):

| Command | Purpose |
|---|---|
| `h360-setup` | Non-interactive site setup: regional and security baseline, Hostel Settings, first Hostel Administrator |
| `h360-migrate` | Legacy data migration from CSV/XLSX with a JSON mapping, `--dry-run` rolls back (TR 5) |
| `h360-export` | Exit export: CSV + XLSX per DocType, core records, SQL dump, files, manifest; optional AES-256-GCM encryption (TR 22) |
| `h360-verify-export` | Verify an export folder or archive against its manifest; with `--site` the result is recorded in Exit Export Log |
| `h360-purge-institution-data` | Delete the institution's data after the exit export is acknowledged; dry run unless `--execute`; issues a signed deletion certificate |
| `h360-verify-deletion-certificate` | Check a deletion certificate's HMAC signature |
| `h360-demo-data` | Deterministic demonstration/UAT data set |

## 3. Roles

| Role | Desk | 2FA | Purpose |
|---|---|---|---|
| Hostel Administrator | yes | yes | Everything in Hostel360, settings |
| Hostel Warden | yes | yes | Chief warden: all hostels, final approvals, escalations |
| Deputy Warden | yes | yes | Approvals and operations for assigned hostels/blocks |
| Hostel Office Staff | yes | yes | Registrations, allotment, fees, receipts |
| Mess Manager | yes | yes | Mess stock, menu, indents, reductions, billing |
| Mess Staff | yes | yes | Stock receipts/issues |
| Hostel Security | yes | yes | Gate check-out and return of students on approved leave, for assigned hostels |
| HyFib Operations | yes | yes | Alerts, health, support tickets, performance reports, security incidents |
| Institution Auditor | yes | yes | Read-only dashboards, reports, monitoring (tender 6.12) |
| Hostel Student | no | OTP | Student portal |
| Hostel Parent | no | OTP | Parent portal |

Constants live in `hostel360/setup/roles.py`: `STAFF_ROLES` (the eight institution desk
roles plus System Manager), `GATE_ROLES = STAFF_ROLES | {Hostel Security}` (Hostel Security
is deliberately kept out of `STAFF_ROLES`, so gate staff never see student records, fees or
reports), `GRIEVANCE_HANDLER_ROLES = STAFF_ROLES − {Institution Auditor}` (auditors observe,
they are never assigned grievances), `APPROVER_ROLES`, `ADMIN_ROLES`, `PORTAL_ROLES`.
Two-factor authentication is also forced on for Frappe's own System Manager and
Administrator roles (`TWO_FACTOR_CORE_ROLES`).

### Desk homes and areas

Each desk role sees the workspaces of the work it does, and nothing else is offered on its
desk. A workspace's roles decide who may open it; the Hostel360 dock shows an area only to
the people who may open its workspace (`setup.landing.filter_dock`), so read access granted
for one job (a mess manager reading fee invoices for mess billing) does not put another
area's menu in front of them. Records stay reachable through links and search as the
permission matrix below allows. Hostel360 opens at `/staff`, which sends each person to their
home: the first workspace of `LANDING_WORKSPACES` they may open (`setup.landing`).

| Role | Home | Other areas on the dock |
|---|---|---|
| Hostel Administrator | Hostel Admin | People and Rooms, Fees, Mess, Student Services, Gate, HyFib Operations, Messaging, Data Migration |
| Hostel Warden, Deputy Warden | Hostel Admin | People and Rooms, Fees, Mess, Student Services |
| Hostel Office Staff | Hostel Admin | People and Rooms, Fees, Student Services, Messaging |
| Mess Manager | Mess | Student Services |
| Mess Staff | Mess | – |
| Hostel Security | Gate (gate pass, leave out now) | – |
| Institution Auditor | Hostel Admin | People and Rooms, Fees, Mess, Student Services, HyFib Operations, Messaging, Data Migration |
| HyFib Operations | HyFib Operations | Messaging, Data Migration |

### Hostel and block scoping

When a Hostel Staff record with a designation in `HOSTEL_SCOPED_DESIGNATIONS` (Deputy
Warden, Office Staff, Mess Staff, Security) is saved, User Permissions (`allow = Hostel`)
are synchronised for each assigned hostel, so every DocType with a `hostel` Link field is
filtered automatically. Every transactional DocType therefore carries a `hostel` Link,
copied from the student's `current_hostel`, or `last_hostel` for a student who has left.

Row-level rules that User Permissions cannot express are registered in `hooks.py`
(`permission_query_conditions` and `has_permission`):

| DocTypes | Rule | Module |
|---|---|---|
| Hostel Student, Room Allotment | A Hostel Staff assignment with a `block` limits the person to students living in that block; vacated students are scoped by `last_hostel` | `hostel_people.permissions` |
| Hostel Guardian | Scoped through its wards: visible when any ward is visible, editable only when every ward is | `hostel_people.permissions` |
| Hostel Grievance | Confidential grievances (always the case for Ragging or Harassment) only for the Warden, Hostel Administrators, System Managers and the assignee; Mess Managers see Mess and Food grievances only | `student_services.permissions` |
| Home Visit Leave, Bonafide Certificate Request, Hostel Feedback | Hostel scoping judged on the stored record (defence in depth for read paths that build a document from client JSON) | `student_services.permissions` |
| Message Log | Rows about a confidential grievance only for the people who may open that grievance | `hostel_communication.permissions` |
| Hostel Mess, Mess Menu, Mess Stock Entry, Mess Stock Ledger Entry, Mess Indent, Mess Expense, Mess Bill Run, Mess Bill, Mess Extra Charge, Mess Reduction Application | A hostel-scoped user sees only the messes that serve at least one of their hostels | `hostel_mess.permissions` |

Every rule reads the deciding values (hostel, block, confidentiality, assignee) from the
database for an existing record, so a request cannot open a record by rewriting them.
Deputy Wardens and Office Staff have no share permission on students, guardians and
allotments. Approver checks for leave, bonafide requests and mess reductions use the
same staff scope (`hostel_people.services.staff_covers_student`).

Portal users never get DocType permissions. All portal access goes through
`hostel360.api.portal`, which resolves the caller with `hostel360.utils.context` and checks
ownership explicitly.

### Permission matrix (DocPerm rows)

R = read, W = write/create, S = submit/cancel, D = delete, X = export/print/report.

| DocType group | Admin | Warden | Deputy W. | Office | Mess Mgr | Mess Staff | Security | Auditor | HyFib Ops | System Manager |
|---|---|---|---|---|---|---|---|---|---|---|
| Setup masters (Hostel…Bed, Academic Year, Programs) | RWDX | RX | RX | RX | R | R | – | RX | – | RWDX |
| Hostel Settings | RW | R | R | R | R | – | – | R | – | RW |
| Students, Guardians | RWDX | RWX | RWX | RWX | R | – | – | RX | – | RWDX |
| Student medical notes (permlevel 1) | RW | RW | RW | – | – | – | – | – | – | RW |
| Student Bulk Import | RWD | RW | RW (own) | RW (own) | – | – | – | – | – | RWD |
| Hostel Staff | RWDX | RX | R | R | – | – | – | RX | – | RWDX |
| Room Allotment | RWSX | RWSX | RWSX | RWSX | R | – | – | RX | – | RWSDX |
| Fees (structure, invoice, payment) | RWSX | RX | RX | RWSX | R | – | – | RX | – | RWSDX |
| Payment settings / transactions | RW / RX | – | – | – / RX | – | – | – | – / RX | R / R | RW / RWX |
| Mess (stock, menu, indent, expense, billing) | RWSX | RX | RX | R | RWSX | RW (stock entry, menu) | – | RX | – | RWSDX |
| Mess Reduction Application | RWX | RWX | RWX | RX | RWX | R | – | RX | – | RWDX |
| Leave, Grievance, Feedback, Bonafide | RWX | RWX | RWX | RWX | R (grievance: mess categories) | – | R (leave, through the Gate Pass page) | RX | – | RWDX |
| Messaging settings/templates | RW | – | – | – | – | – | – | – | RW | RW |
| Message Log (subject, message, payload at permlevel 1) | R (+ permlevel 1) | – | – | R | – | – | – | R | R | RD (+ permlevel 1) |
| Ops (alerts, settings, metrics, backups, reports) | R | – | – | – | – | – | – | R (alerts, metrics, backups, reports) | RWX | RWX |
| Security Incident | R | – | – | – | – | – | – | RX | RWX | RWDX |
| Ops Ingest Receipt | – | – | – | – | – | – | – | – | RX | RDX |
| Support Ticket (status, assignee, resolution at permlevel 1) | RW | RW | RW | RW | RW | RW | – | R | RWX | RWX |
| Migration Run / Migration Record Map | RWX / RX | – | – | – | – | – | – | R / – | R / R | RWDX / RDX |
| Exit Export Log | RX | – | – | – | – | – | – | R | R | RDX |

## 4. Data model

Conventions: every DocType has `track_changes: 1` (audit trail), `sort_field: creation`,
`sort_order: DESC`. Money fields are `Currency` with `options: ""` (INR via system
currency). Mobile numbers are 10-digit Indian numbers normalised with
`hostel360.utils.validation.normalize_mobile`. Dates are site-local (Asia/Kolkata).
Fields the system maintains (statuses, links set by actions, counters) are `read_only`
and are also protected on the server (`hostel360.hostel_people.protection`,
`assert_unchanged` in Student Services), so a crafted API save cannot change them.

### 4.1 Hostel Setup (`hostel360/hostel_setup`)

**Hostel Settings** (single)
`institution_name` Data, `institution_short_name` Data (default "ACGCET"),
`institution_address` Small Text, `institution_logo` Attach Image, `hostel_website_url` Data,
`office_phone` Data, `office_email` Data, `current_academic_year` Link Hostel Academic Year,
`portal_url` Data (public base URL, e.g. https://hostel.accet.ac.in),
`student_portal_enabled` Check (1), `parent_portal_enabled` Check (1),
`leave_requires_parent_approval` Check (1), `leave_return_grace_hours` Int (2),
`grievance_escalation_hours` Int (48), `grievance_urgent_escalation_hours` Int (4),
`bonafide_signatory_name` Data, `bonafide_signatory_designation` Data,
`bonafide_validity_days` Int (90), `support_phone` Data ("+91 96060 60596"),
`support_email` Data ("support@hyfib.com"), `privacy_notice_version` Data ("1.0"),
`privacy_notice` Text Editor.

**Hostel Academic Year** — autoname `field:academic_year` (e.g. "2026-2027");
`academic_year` Data, `start_date` Date, `end_date` Date, `is_current` Check (desk action **Make Current**).

**Academic Department** — autoname `field:department_name`; `department_name`, `department_code`.

**Academic Program** — autoname `field:program_name`; `program_name`, `department` Link Academic Department,
`degree_level` Select (UG/PG/Doctoral/Diploma), `duration_years` Int, `is_active` Check.

**Room Facility** — autoname `field:facility_name`; `facility_name`, `description`.
Child **Hostel Room Facility**: `facility` Link Room Facility.

**Hostel** — autoname `field:hostel_code` ; `hostel_name` Data, `hostel_code` Data (unique, short, e.g. "BH1"),
`gender` Select (Male/Female/Mixed), `warden` Link User, `address` Small Text, `is_active` Check,
`total_capacity` Int (ro), `occupied_beds` Int (ro), `vacant_beds` Int (ro), `occupancy_percent` Percent (ro),
`facilities` Table MultiSelect Hostel Room Facility, `image` Attach Image.

**Hostel Block** — autoname `format:{hostel}-{block_code}`; `hostel` Link Hostel, `block_name`, `block_code`, `description`.

**Hostel Floor** — autoname `format:{block}-F{floor_number}`; `hostel` (fetch), `block` Link Hostel Block, `floor_number` Int, `floor_label` Data.

**Hostel Room** — autoname `format:{hostel}-{room_number}`; `hostel`, `block`, `floor`, `room_number` Data,
`room_type` Select (Single/Double/Triple/Four Sharing/Dormitory), `capacity` Int,
`occupied_beds` Int (ro), `vacant_beds` Int (ro), `status` Select (Available/Partially Occupied/Full/Under Maintenance/Blocked),
`facilities` Table MultiSelect Hostel Room Facility, `is_active` Check, `remarks`.
Creating a room creates `capacity` beds (A, B, C…); changing capacity adds beds or refuses to drop occupied ones.

**Hostel Bed** — autoname `format:{room}-{bed_label}`; `room` Link Hostel Room, `bed_label` Data, `hostel` (fetch),
`block` (fetch), `floor` (fetch), `status` Select (Vacant/Occupied/Reserved/Maintenance), `is_active` Check (ro),
`current_student` Link Hostel Student (ro), `current_student_name` (ro), `current_allotment` Link Room Allotment (ro), `remarks`.
Room and hostel counters are derived from beds (`hostel_setup.occupancy`) and never edited by hand.

### 4.2 Hostel People (`hostel360/hostel_people`)

**Hostel Student** — autoname `field:register_number`.
`register_number` Data (unique), `roll_number` Data, `first_name`, `last_name`, `full_name` (ro),
`gender` Select (Male/Female/Other), `date_of_birth` Date, `blood_group` Select, `photo` Attach Image,
`program` Link Academic Program, `department` Link Academic Department (fetch), `year_of_study` Select (1/2/3/4/5),
`admission_year` Int, `academic_year` Link Hostel Academic Year,
`mobile_number` Data options Phone (unique, normalised), `email` Data options Email,
`address_line1`, `address_line2`, `city`, `district`, `state` (default Tamil Nadu), `pincode`,
`emergency_contact_name`, `emergency_contact_phone`, `medical_notes` Small Text (permlevel 1),
`guardians` Table Hostel Student Guardian,
`status` Select (Applied/Active/Suspended/Vacated/Alumni) default Active,
`validation_status` Select (Pending/Validated/Rejected) default Pending, `validated_by` Link User (ro), `validated_on` Datetime (ro),
`rejection_reason` Small Text (ro),
`profile_confirmed` Check (ro), `privacy_consent_version` Data (ro), `privacy_consent_on` Datetime (ro),
`mess_enrolled` Check (1), `user` Link User (ro),
`current_hostel` Link Hostel (ro), `current_room` Link Hostel Room (ro), `current_bed` Link Hostel Bed (ro), `current_allotment` Link Room Allotment (ro),
`last_hostel` Link Hostel (ro): the hostel the student lives in now or lived in last; kept after vacating and restored when an allotment is cancelled.
Actions: Validate, Reject (with reason), Create Portal User (Website User with role Hostel Student, random unusable password), Unlock Sign-in.

Validation before sign-in (3.1): only students whose registration is `Validated` and whose
status is not Vacated or Alumni can sign in to the portal, and parents only while linked to
at least one such student (4.1). The office validates the registration against the college
register; confirm-details and the privacy notice happen on the first sign-in after
validation. The same rule is asked again on every portal call
(`hostel360.auth.events.portal_login_blocked_reason`), so a session opened before a record
was rejected, closed or un-validated stops working with the reason. Full portal features
need a Validated student with status Active; a validated student who is not Active (for
example Suspended) keeps access to their dues, receipts and profile.

Child **Hostel Student Guardian**: `guardian` Link Hostel Guardian, `guardian_name` (fetch), `relation` Select
(Father/Mother/Guardian/Brother/Sister/Other), `mobile_number` (fetch), `is_primary` Check, `can_approve_leave` Check (1).

**Hostel Guardian** — autoname naming series `GRD-.#####`; `guardian_name`, `relation` Select, `mobile_number` (unique, normalised),
`email`, `occupation`, `address` Small Text, `validation_status` Select (Pending/Validated/Rejected), `user` Link User (ro),
`privacy_consent_version`, `privacy_consent_on`. Actions: Create Portal User, Unlock Sign-in.
Validation of parents (4.1): validated when linked to a validated student and the mobile matches the registered number.

**Hostel Staff** — autoname naming series `STF-.#####`; `staff_name`, `employee_id` (unique),
`designation` Select (Hostel Administrator/Warden/Deputy Warden/Office Staff/Mess Manager/Mess Staff/Security/Housekeeping/Other),
`mobile_number`, `email`, `photo`, `is_active` Check, `create_user` Check, `send_welcome_email` Check (1), `user` Link User (ro),
`assignments` Table Hostel Staff Assignment (`hostel` Link Hostel, `block` Link Hostel Block optional).
Saving with `create_user` creates a System User with roles from `DESIGNATION_ROLES` (Security → Hostel Security)
and syncs User Permissions. A block assignment limits the person to students living in that block. Deactivating the
staff record disables the desk login.

**Room Allotment** (submittable) — naming series `ALT-.YYYY.-.#####`.
`student` Link Hostel Student, `student_name` (fetch), `gender` (fetch), `academic_year`, `hostel`, `block` (ro), `room`, `bed` Link Hostel Bed,
`allotment_type` Select (New/Transfer), `previous_allotment` Link Room Allotment, `from_date` Date, `to_date` Date (optional),
`status` Select (Active/Transferred/Vacated/Cancelled), `vacated_on` Date, `vacate_reason` Small Text, `remarks`.
On submit: lock bed row, require bed Vacant and student without another Active allotment (unless Transfer),
check hostel gender, set bed Occupied and the student's `current_*` and `last_hostel` fields, update room/hostel counters.
Whitelisted controller methods: `transfer(new_bed, from_date, remarks)` → new allotment, old status Transferred;
`vacate(vacated_on, reason)` → status Vacated, bed Vacant. History is never deleted.
A daily job (`hostel_people.tasks.close_ended_allotments`) vacates allotments whose `to_date` has passed.

Registration-desk exceptions to hostel scoping: (a) a student who lives in no hostel now may be given a New allotment by
staff who can submit an allotment for that bed; they find the student by exact register or mobile number
(`hostel_people.queries.allottable_student_query`, `RoomAllotment.admits_returning_student`); (b) staff with Hostel Student
write permission may link an existing guardian of another hostel's student by exact mobile number
(`hostel_people.queries.guardian_link_query`); viewing and editing that guardian still follow the ward rule.

**Student Bulk Import** — upload XLSX/CSV (template downloadable), validate (every row checked, nothing written,
preview stored), import in background on the long queue, creates/updates Hostel Student + Hostel Guardian + links
(+ room allotment, + portal users when asked), downloadable "rows to fix" report.
Fields: `import_file` Attach, `academic_year`, `status` Select (Draft/Validated/Importing/Completed/Completed with Errors/Failed),
`update_existing` Check (1), `allot_rooms` Check (1), `create_portal_users` Check, `total_rows`, `imported_rows`,
`skipped_rows`, `failed_rows`, `started_on`, `finished_on`, `log` Code. Students are matched by register number and
guardians by mobile number; each row runs in its own savepoint. Deputy Wardens and Office Staff see only their own imports. A returning-student row is allotted first and then updated; rows for students the
importer cannot update are refused in the preview; a guardian shared with another hostel is linked unchanged, with a warning.

Desk page **room-board**: visual hostel→block→floor→room→bed grid, colour by status, click to allot/transfer/vacate,
reserve or send a bed to maintenance.

### 4.3 Hostel Fees (`hostel360/hostel_fees`)

**Hostel Fee Head** — autoname `field:fee_head_name`; `fee_head_name`, `fee_category` Select (Hostel/Mess/Deposit/Fine/Other),
`is_refundable` Check, `is_active` Check, `description`. Defaults installed: Hostel Rent, Establishment Charges, Electricity Charges,
Caution Deposit (refundable), Mess Advance, **Mess Charges**, **Mess Extras**, Fine, Other.

**Hostel Fee Structure** — autoname `field:structure_name`; `structure_name`, `academic_year`, `hostel` Link Hostel (optional),
`year_of_study` Select (blank = all), `program` Link Academic Program (optional), `items` Table Hostel Fee Structure Item
(`fee_head`, `amount`, `description`), `total_amount` (ro), `due_date` Date, `is_active` Check, `notes`,
`publish_status` Select (Not Published/Queued/In Progress/Completed/Completed with Errors/Failed) (ro), `invoices_created` (ro),
`last_published_on` (ro), `last_published_by` (ro), `publish_log` (ro).
Action **Publish Dues** (background job, long queue, with a preview first): one Hostel Fee Invoice per matching active,
validated student without an existing invoice for this structure (optionally re-issuing cancelled ones); notifies `fee_due_published`.

**Hostel Fee Invoice** (submittable) — naming series `INV-.YYYY.-.#####`.
`student`, `student_name` (fetch), `hostel` (fetch current_hostel), `academic_year`, `invoice_type` Select
(Hostel Fee/Mess Bill/Fine/Other), `fee_structure` Link, `posting_date`, `due_date`, `items` Table Hostel Fee Invoice Item
(`fee_head`, `description`, `amount`), `total_amount`, `paid_amount` (ro), `migrated_paid_amount` (ro, amounts paid before
migration), `outstanding_amount` (ro), `status` Select (Draft/Unpaid/Partly Paid/Paid/Overdue/Cancelled),
`reference_doctype` Link DocType, `reference_name` Dynamic Link, `remarks`, `last_reminder_on` (ro), `reminder_count` (ro).
Desk action **Record Payment** (counter receipt).

**Hostel Payment** (submittable; the receipt) — naming series `RCPT-.YYYY.-.#####`.
`student`, `student_name`, `hostel`, `payment_date`, `mode_of_payment` Select (Online/Cash/UPI/Demand Draft/Cheque/Bank Transfer),
`amount`, `reference_no`, `reference_date`, `bank_name`, `payment_transaction` Link Hostel Payment Transaction, `is_test` Check (ro,
receipts of test-mode gateway payments, excluded from collection figures),
`allocations` Table Hostel Payment Allocation (`invoice` Link Hostel Fee Invoice, `invoice_type` (fetch), `due_date` (fetch),
`allocated_amount`, `outstanding_before`), `total_allocated` (ro), `unallocated_amount` (ro), `received_by` Link User, `remarks`,
`cancelled_on` (ro), `cancelled_by` (ro). Submit updates invoice paid/outstanding/status; cancel reverses.

**Hostel Payment Transaction** — naming series `PTX-.YYYY.-.######`; `student`, `student_name`, `hostel`, `invoices` Table Hostel
Payment Transaction Invoice (`invoice`, `invoice_type`, `amount`), `amount`, `currency` (INR), `gateway` Data,
`gateway_order_id` (unique), `gateway_payment_id`, `payment_method`, `test_mode` Check, `status` Select
(Initiated/Pending/Success/Failed/Expired/Refunded), `failure_reason`, `refunded_amount`, `verification_failed` (hidden),
`completed_on`, `payment` Link Hostel Payment, `initiated_by` Link User, `ip_address`, `gateway_response` Code JSON (permlevel 1),
`reconciled_on` Datetime, `last_checked_on`, `remarks`.
Desk actions: **Check with Gateway**, **Close as Expired** (an order the gateway can no longer read), **Mark Refunded**.

**Hostel Payment Settings** (single) — `enabled`, `gateway` Select (Razorpay/PayU), `test_mode` Check, `merchant_display_name`,
`transaction_expiry_minutes` Int (60), Razorpay: `key_id` Data, `key_secret` Password, `webhook_secret` Password;
PayU: `payu_merchant_key` Data, `payu_merchant_salt` Password; `test_payers` Table MultiSelect Hostel Payment Test Payer
(`student`): while `test_mode` is on only these students (and administrators trying the flow) may pay online;
reminders: `send_fee_reminders` Check (1), `reminder_days_before_due` Int (3), `reminder_interval_days` Int (3),
`max_reminders_per_invoice` Int (10; sites installed before 1.1 keep a stored 0 = no limit). Desk action **Test Connection**.

Gateway adapters in `hostel360/hostel_fees/gateways/` implement `PaymentGateway`:
`is_configured`, `create_order`, `checkout_options(tx, context)`, `fetch_order_status`, `fetch_payment`, `test_connection`,
`verify_checkout` (modal checkouts), `verify_return` and `return_order_id` (redirect checkouts), `verify_webhook`,
`capture_payment`, plus the class attributes `checkout_mode` (`modal` for Razorpay, `redirect` for PayU) and
`reusable_orders` (True when a failed attempt can be retried on the same order). Amounts cross the adapter boundary in
paise as integers. Collections settle directly to ACGCET's merchant account. The browser is never trusted: every
payment is read back from the gateway before a receipt is issued, and reconciliation (every 15 minutes, daily at 23:30)
settles payments whose callback and webhook never arrived.

### 4.4 Hostel Mess (`hostel360/hostel_mess`)

**Mess Settings** (single) — `reduction_min_days` Int (3), `reduction_advance_notice_days` Int (1),
`reduction_approver` Select (Deputy Warden/Mess Manager), `max_reduction_days_per_month` Int (0 = no limit),
`bill_due_days` Int (10), `bill_rounding` Select (Nearest Rupee/None), `low_stock_alert` Check,
`require_indent_for_receipts` Check (0; when on, stock receipts must follow an approved indent),
`indent_warden_approval_above` Currency (0 = no limit; indents estimated above it need a Warden or Administrator).

**Hostel Mess** — autoname `field:mess_name`; `mess_name`, `hostels` Table MultiSelect Hostel Mess Hostel (`hostel`),
`mess_manager` Link User, `is_active`, `description`, `billed_upto` Date (ro; the end of the last published bill run:
stock and expenses on or before it are closed).

**Mess Item** — autoname `field:item_name`; `item_name`, `local_name`, `item_code`, `category` Select (Grains/Pulses/Vegetables/Fruits/Dairy/Meat and Eggs/Oil and Ghee/Spices/Fuel/Cleaning/Other),
`uom` Select (Kg/Gram/Litre/Millilitre/Nos/Packet/Dozen/Cylinder), `reorder_level` Float, `reorder_qty` Float, `last_purchase_rate` (ro),
`is_perishable`, `is_active`, `stock_revision` Int (hidden; written by every stock movement of the item to serialise them).

**Mess Supplier** — autoname `field:supplier_name`; `supplier_name`, `gstin`, `contact_person`, `mobile_number`, `email`, `address`, `is_active`.

**Mess Stock Entry** (submittable) — naming series `MSE-.YYYY.-.#####`; `entry_type` Select (Receipt/Issue/Adjustment/Return to Supplier),
`mess`, `posting_date`, `posting_time`, `supplier`, `indent` Link Mess Indent, `supplier_invoice_no`, `supplier_invoice_date`,
`items` Table Mess Stock Entry Item (`item`, `uom` fetch, `qty`, `rate`, `amount`, `available_qty` ro, `valuation_rate` ro),
`total_amount`, `remarks`. Submit writes Mess Stock Ledger Entry rows (moving-average valuation); back-dated entries revalue
later rows; issue quantity may not exceed balance; cancel reverses; nothing on or before `billed_upto` may change.

**Mess Stock Ledger Entry** (read-only log, autoincrement) — `mess`, `item`, `posting_date`, `posting_time`, `posting_datetime`,
`voucher_type`, `voucher_no`, `voucher_detail_no`, `entry_type`, `actual_qty`, `incoming_rate`, `valuation_rate`,
`qty_after_transaction`, `stock_value`, `stock_value_difference`, `is_cancelled`.

**Mess Indent** (submittable) — naming series `IND-.YYYY.-.#####`; `mess`, `posting_date`, `required_by`, `mess_menu` Link Mess Menu
(the approver sees the week's planned meals), `supplier`, `items` Table Mess Indent Item
(`item`, `uom`, `current_stock` ro, `required_qty`, `estimated_rate`, `estimated_amount`), `total_estimated_amount` (ro), `remarks`,
`status` Select (Draft/Pending Approval/Approved/Rejected/Ordered/Received/Cancelled), `requested_by` Link User (ro),
`approved_by`, `approved_on`, `approval_remarks`, `ordered_on`, `stock_entry` Link Mess Stock Entry, `received_on`.
Approvers: Mess Manager, Warden, Administrator. The person who raised an indent cannot approve it (except Warden/Administrator),
and indents above `indent_warden_approval_above` need a Warden or Administrator. Actions: Get Items Below Reorder Level,
Approve, Reject, Mark as Ordered, Receive Goods (drafts the Receipt).

**Mess Menu** — autoname `format:{mess}-{week_start_date}`; `mess`, `week_start_date` (Monday), `week_end_date` (ro),
`status` Select (Draft/Published), `items` Table Mess Menu Item (`menu_date` Date, `day` Select Monday…Sunday (ro),
`meal` Select (Breakfast/Lunch/Snacks/Dinner), `dishes` Small Text, `is_special` Check), `notes`, `published_on`, `published_by`.
Actions: Copy Previous Week, Add Days and Meals, Publish (notifies `menu_published`, in-app).

**Mess Reduction Application** — naming series `MRA-.YYYY.-.#####`; `student`, `student_name`, `hostel`, `mess`,
`from_date`, `to_date`, `number_of_days` (ro, inclusive), `reason` Select (Home Visit/Medical/Internship/Sports or Cultural Event/College Tour/Other),
`description`, `home_visit_leave` Link Home Visit Leave, `status` Select (Pending/Approved/Rejected/Cancelled),
`approver` Link User (ro), `decided_on` Datetime (ro), `approval_remarks`, `applied_by` Link User.
Rules: `number_of_days` ≥ `reduction_min_days`; no overlap with Pending/Approved applications; `from_date` ≥ today +
advance notice (approvers may override); per-month cap. Approval by the configured role (and Admin/Warden), limited to the
students the approver's hostel/block scope covers.

**Mess Expense** (submittable) — naming series `MEX-.YYYY.-.#####`; `mess`, `posting_date`, `expense_type` Select
(Gas/Fuel/Wages/Electricity/Water/Maintenance/Other), `amount`, `supplier`, `bill_no`, `attachment`, `remarks`.

**Mess Extra Charge** — naming series `MXC-.YYYY.-.#####`; `student`, `student_name`, `hostel`, `mess`, `charge_date`,
`charge_type` Select (Guest Meal/Special Food/Extra Item/Breakage/Other), `description`, `amount`, `status` Select (Unbilled/Billed), `mess_bill` Link.

**Mess Bill Run** (submittable) — autoname `format:{mess}-{from_date}`; `mess`, `from_date`, `to_date` (at most 92 days),
`computed_on`, `due_date`, `provision_cost` (ro), `other_expenses` (ro), `other_credits` Currency, `credits_remarks`,
`total_cost` (ro), `total_student_days` (ro), `daily_rate` (ro, 2 decimals), `bills_count` (ro), `total_extras` (ro),
`total_billed` (ro), `rate_rounding_difference` (ro), `rounding_adjustment` (ro), `status` Select (Draft/Computed/Published/Cancelled).
Dividing system:
```
total_cost       = Σ issue value in period + Σ submitted Mess Expense − other_credits
chargeable_days  = enrolled_days(student, period) − approved_reduction_days(student, period)   (≥ 0)
daily_rate       = round(total_cost ÷ Σ chargeable_days, 2)
bill             = chargeable_days × daily_rate + Σ unbilled Mess Extra Charge in period
```
`enrolled_days` comes from submitted Room Allotments in the hostels the mess serves, merged day by day, and counts only
the days on which `mess_enrolled` was in force (dated from the Hostel Student version history).
Actions: **Compute** (background; creates Mess Bill rows, idempotent), **Publish** (submit: creates one Hostel Fee Invoice
per bill with `invoice_type = Mess Bill`, fee heads Mess Charges/Mess Extras; notifies `mess_bill_published`; sets
`billed_upto`). Publish and cancel run in the background on the long queue for runs above 250 bills.
Cancel is allowed only while all invoices it created are unpaid. A `Hostel Fee Invoice` `on_submit` hook
(`hostel_mess.billing.sync_amended_invoice`) keeps `Mess Bill.fee_invoice` pointing at an invoice amended by the office.

**Mess Bill** — autoname `format:{mess_bill_run}-{student}`; `mess_bill_run`, `mess`, `student`, `student_name`, `hostel`,
`from_date`, `to_date`, `enrolled_days`, `reduction_days`, `chargeable_days`, `daily_rate`, `mess_charge`, `extras_amount`,
`rounding_adjustment`, `total_amount`, `fee_invoice` Link Hostel Fee Invoice, `status` Select (Draft/Published/Cancelled).

### 4.5 Student Services (`hostel360/student_services`)

**Home Visit Leave** — naming series `LV-.YYYY.-.#####`; `student`, `student_name`, `hostel`, `room`,
`from_datetime`, `to_datetime`, `number_of_days` (ro), `destination`, `reason`, `contact_during_leave`, `applied_by` Select (Student/Parent/Warden),
`applied_by_user` Link User, `parent_approval_status` Select (Pending/Approved/Rejected/Not Required), `parent_guardian` Link Hostel Guardian,
`parent_guardian_name` (fetch), `parent_action_on` Datetime, `parent_remarks`, `warden_approval_status` Select (Pending/Approved/Rejected), `warden_user` Link User,
`warden_action_on`, `warden_remarks`, `status` Select (Pending Parent Approval/Pending Warden Approval/Approved/Rejected/Cancelled/Checked Out/Returned/Overdue),
`apply_mess_reduction` Check, `mess_reduction_days` (ro; full mess days missed under the 12:00 cut-off rule),
`mess_reduction` Link Mess Reduction Application, `cancelled_by` (ro), `checked_out_on`, `returned_on`, `overdue_since`.
Flow: student applies → parent approval (skipped when not required or applied by the parent) → warden approval
(Warden, Administrator, or a Deputy Warden covering the student's hostel/block) → Approved (creates a Mess Reduction
Application when requested and long enough) → **Checked Out** and **Returned** recorded at the gate (from two hours
before the leave starts; server time only, no back-dating) → Overdue flagged hourly once `to_datetime` plus
`leave_return_grace_hours` has passed. Cancelling a leave withdraws its pending or approved reduction.

**Hostel Grievance** — naming series `GRV-.YYYY.-.#####`; `student`, `student_name`, `raised_by_user` (ro), `hostel`, `room`, `category` Select
(Room and Furniture/Electrical/Plumbing/Cleanliness/Mess and Food/Water/Internet/Security/Health/Ragging or Harassment/Staff Behaviour/Other),
`subject`, `description` Text, `priority` Select (Low/Medium/High/Urgent), `is_confidential` Check,
`status` Select (Open/In Progress/Escalated/Resolved/Closed/Reopened), `assigned_to` Link User, `assigned_on`, `escalation_level` Int,
`escalated_on`, `sla_due_by` Datetime, `sla_breached` Check, `reopen_count`, `resolution` Text, `resolved_by`, `resolved_on`,
`resolution_hours`, `closed_on`, `student_rating` Rating, `student_feedback`,
`updates` Table Hostel Grievance Update (`update_on`, `update_by`, `update_by_name`, `message`, `message_template` (hidden),
`message_args` (hidden), `is_internal`, `status_change`): the timeline; internal notes are never shown in the portal and
system entries are stored in English and translated when read.
"Ragging or Harassment" is always confidential, Urgent, and escalated to the Warden immediately. SLA: `grievance_escalation_hours`
(48) or `grievance_urgent_escalation_hours` (4) for Urgent; an hourly job escalates overdue grievances one level per SLA period
(up to level 3). Assignees are limited to `GRIEVANCE_HANDLER_ROLES`. A resolved grievance can be reopened by the student within 7 days.

**Hostel Feedback** — naming series `FBK-.YYYY.-.#####`; `student`, `student_name`, `hostel`, `feedback_type` Select (Mess/Hostel/Facilities/General),
`period` Data (YYYY-MM), `food_quality`, `cleanliness`, `staff_behaviour`, `facilities`, `overall` (Rating), `comments`,
`is_anonymous` Check, `action_taken`, `submission_key` (hidden, unique: one feedback per student, type and period).
Anonymous feedback stores no student link and no submitter, only the keyed hash in `submission_key` that stops a
second submission for the same month.

**Bonafide Certificate Request** — naming series `BON-.YYYY.-.#####`; `student`, `student_name`, `program`, `department`, `year_of_study`,
`academic_year`, `hostel`, `room` (copied at request time), `purpose` Select
(Bank Loan/Scholarship/Passport/Visa/Internship/Address Proof/Other), `purpose_details`, `status` Select (Pending/Approved/Rejected/Issued),
`approved_by`, `approved_on`, `rejection_reason`, `certificate_number` (ro, `BC/YYYY/#####` on approval), `issue_date`, `valid_till`,
`verification_code` (ro, random 16-char), `issued_on`, `issued_by`. Approval by the Warden, an Administrator or the Deputy
Warden of the hostel; **Mark Handed Over** (status Issued) by office staff. Print format "Bonafide Certificate" with QR code
to `/verify-certificate?code=…`. Public page `/verify-certificate` (and the rate-limited JSON endpoint
`hostel360.student_services.verification.verify`) confirms authenticity showing certificate number, masked name, programme,
issue date and validity.

Desk page **approvals**: one inbox for wardens/mess managers — pending leaves, mess reductions, bonafide requests, indents,
escalated grievances — approve/reject inline with remarks, limited to the user's hostels and blocks.

Desk page **gate-pass** (Hostel Security, Deputy Warden, Warden, Administrator): students due to leave today and students
who are away (overdue first), search by register number, roll number, name or room, photo, and **Check Out** / **Returned**
buttons that run the leave's own actions at the server's current time. Gate staff cannot open student records.

### 4.6 Hostel Communication (`hostel360/hostel_communication`)

**Messaging Settings** (single) — SMS: `sms_enabled`, `sms_provider` Select (MSG91/Generic HTTP), `sms_api_url`, `sms_auth_key` Password,
`sms_sender_id` (DLT header), `dlt_entity_id`, `alert_sms_template_id`, `alert_dlt_template_id`, and for Generic HTTP:
`sms_http_method`, `sms_payload_format`, `sms_auth_header`, `sms_auth_param`, `sms_mobile_format`, `sms_success_keyword`,
`sms_param_*`, `sms_unicode_value`, `sms_extra_params`;
WhatsApp: `whatsapp_enabled`, `whatsapp_phone_number_id`, `whatsapp_business_account_id`, `whatsapp_access_token` Password,
`whatsapp_api_version` ("v21.0"), `alert_whatsapp_template`, `alert_whatsapp_language`;
`email_enabled` Check (uses Frappe Email Account);
OTP: `otp_channels` Select (SMS/WhatsApp/SMS and WhatsApp; the mobile channels), `otp_email_enabled` Check (0; codes may also be
e-mailed, needs `email_enabled` and a default outgoing Email Account that sends by SMTP), `otp_length` Int (6), `otp_expiry_seconds` Int (300), `otp_max_attempts` Int (5),
`otp_resend_cooldown_seconds` Int (60), `otp_max_sends_per_hour` Int (5), `otp_max_sends_per_ip_per_hour` Int (20),
`otp_trusted_networks` Small Text (campus egress CIDRs exempt from the per-IP limit);
`test_mode` Check (log only, do not send), `prefer_whatsapp` Check, `provider_timeout_seconds` Int (10),
`failure_alert_threshold` Int (5). Desk action **Send Test Message**.

**Message Template** — autoname `format:{event}-{channel}` for English; translations are named `{event}-{channel}-{language}`;
`event` Select (see events), `channel` Select (SMS/WhatsApp/Email/In-App), `language` Select (en/ta), `enabled`, `event_label` (ro),
`subject`, `body` Code (Jinja, sandboxed), `available_variables` (ro), `dlt_template_id`, `msg91_template_id`,
`whatsapp_template_name`, `whatsapp_language` (en/en_US/en_GB/ta), `template_parameters`, `standard_hash` (hidden).
Every event ships in English and Tamil; a shipped template is updated on migrate only while nobody has edited it.
Sign-in codes (`otp_login`) can be sent by SMS, WhatsApp or e-mail, never to the in-app inbox; an e-mailed code goes straight to the
SMTP server through `send_otp_email`, never through the Email Queue or `notify`, and its subject never carries the code. Desk action **Preview**.

**Message Log** — autoname hash; `channel`, `event`, `recipient` (masked), `recipient_user`, `reference_doctype`, `reference_name`,
`template`, `provider`, `status` Select (Queued/Sent/Failed/Skipped), `provider_message_id`, `error`, `attempts` Int, `sent_on`,
`next_retry_on`; `subject`, `message` and `payload` at **permlevel 1** (Hostel Administrator and System Manager only).
Never stores OTP values. Desk action **Retry Now**.

Events (constants in `hostel360/hostel_communication/events.py`):
`otp_login`, `student_validated`, `room_allotted`, `fee_due_published`, `fee_reminder`, `payment_received`,
`mess_bill_published`, `menu_published`, `mess_reduction_submitted`, `mess_reduction_decided`,
`leave_submitted`, `leave_parent_approval_required`, `leave_warden_approval_required`, `leave_decided`, `leave_overdue`,
`leave_checked_out`, `leave_returned`,
`grievance_received`, `grievance_assigned`, `grievance_updated`, `grievance_escalated`, `grievance_resolved`,
`bonafide_requested`, `bonafide_decided`, `support_ticket_update`, `mess_low_stock`.
`direct_message` is internal (operations alerts sent through `send_direct`) and has no template.

### 4.7 Hostel Operations (`hostel360/hostel_operations`) — HyFib alerting

**Ops Alert Settings** (single) — `enabled`, `environment` Select (Production/Staging/Development), `instance_label`,
`critical_renotify_minutes` Int (60), `channels` Table Ops Alert Channel
(`channel_type` Select (Email/SMS/WhatsApp/Slack Webhook/Microsoft Teams Webhook/Google Chat Webhook/Generic Webhook),
`target` Data, `secret_url` Password, `signing_secret` Password, `min_severity` Select (Info/Warning/Critical), `enabled`),
`escalation_levels` Table Ops Escalation Level (`level` Int, `label`, `after_minutes` Int, `min_severity`, `outage_immediate` Check,
`notify_emails`, `notify_mobiles`, `mobile_min_severity`, `mobile_channel` Select (SMS/WhatsApp/SMS and WhatsApp)),
`report_recipients` Small Text (institution officials who receive the Monthly Performance Report),
thresholds: `disk_warning_percent` (80), `disk_critical_percent` (90), `db_latency_warning_ms` (500), `queue_backlog_threshold` (500),
`failed_job_threshold` (10), `scheduler_stale_minutes` (10), `error_spike_threshold` (25 per 15 min), `failed_login_threshold` (30 per 15 min),
`ssl_expiry_warning_days` (21), `payment_failure_threshold` (10 per hour), `pending_payment_minutes` (30),
`message_failure_percent` (20), `backup_max_age_hours` (26), `restore_test_max_age_days` (35), `agent_silence_minutes` (10),
`ticket_response_hours` (1), `ticket_resolution_hours` (4); `ingest_secret` Password (HMAC key for host agents),
`oci_notification_token` Password, `heartbeat_url` Data (dead-man switch), `last_health_check_on`, `last_escalation_run_on`,
`last_heartbeat_sent_on` (ro). Desk actions: Send Test Notification, Run Health Checks Now, reveal/rotate the ingest secret.
Default escalation matrix (bid document 11 §4): L1 HyFib helpdesk 0 min (all issues, 24×7; support@hyfib.com,
+91 96060 60596); L2 technical lead after 120 min unresolved (contact entered at go-live); L3 Director, Santhanakrishnan
Manikandan (+91 97413 80530, manikandan.s@hyfib.com) after 180 min unresolved or immediately for any outage.
The health checks raise a Warning while an escalation level has no contact or the report recipients are empty.

**Ops Alert** — named `ALERT-YYMMDD-XXXXX` (date + random suffix; not a naming series, so concurrent alerts never queue on a
counter row); `alert_key` Data, `open_key` (hidden, unique while open), `title`, `severity` Select (Info/Warning/Critical),
`category` Select (Availability/Performance/Capacity/Backup/Security/Payments/Messaging/Jobs/Errors/SLA/Other),
`source` Select (App/Health Check/Host Agent/OCI Monitoring/Uptime Probe/Security/Support),
`status` Select (Open/Acknowledged/Resolved/Suppressed), `description` Text, `details` Code JSON,
`first_seen`, `last_seen`, `occurrence_count`, `reopened_count`, `duration_minutes`, `acknowledged_by`, `acknowledged_on`,
`resolved_by`, `resolved_on`, `resolved_via`, `resolution_notes`, `escalation_level` Int, `notified_levels`, `last_notified_on`,
`last_notified_event`, `is_outage` Check. A key resolved and raised again within 15 minutes re-opens the same alert.
Desk actions: Acknowledge, Resolve, Declare Security Incident.

**Ops Metric Sample** — `metric` Data, `value` Float, `unit`, `source`, `recorded_at` Datetime (indexed), `host`. Kept 400 days.
**Ops Backup Record** — naming series `BKP-.YYYY.-.#####`; `backup_type` Select (Daily/Weekly/Monthly/Restore Test/Snapshot),
`status` Select (Success/Failed), `host`, `started_at`, `finished_at`, `duration_seconds` (ro), `size_bytes`, `size_display` (ro),
`snapshot_id`, `repository`, `source`, `message`. A failed record raises `backup.<type>.failed` (Critical); a success resolves it
and `backup.stale` / `backup.restore_test.overdue`.
**Ops Ingest Receipt** — autoname `field:receipt_key`; `receipt_key` (`sig:<signature>` or `msg:<message_id>`), `message_type`,
`received_at`, `host`. Written in the same transaction as the ingested data; kept 3 days.
**Security Incident** — naming series `SINC-.YYYY.-.#####`; `title`, `category` Select (the CERT-In categories of the 28 April 2022
directions), `severity` Select (Low/Medium/High/Critical), `status` Select (Open/Reported/Closed/Not Reportable),
`cert_in_reportable` Check (1), `personal_data_involved` Check, `ops_alert` Link Ops Alert, `detected_on` Datetime,
`cert_in_deadline` (ro, detected + 6 h), `reporting_sla_breached` (ro), `reported_to_certin_on`, `cert_in_reference`,
`reported_to_institution_on`, `institution_contact`, `reported_by`, `description`, `affected_systems`, `actions_taken`,
`assessment_notes`, `closed_on` (ro). The health checks raise `security.incident.<name>.reporting` as Warning 4 h after
detection and Critical at 6 h while a report is outstanding.
**Support Ticket** — naming series `TKT-.YYYY.-.#####`; `subject`, `description` Text Editor, `category` Select
(Outage/Bug/Data Correction/Access or Login/How To/Change Request/Other), `priority` Select (Low/Medium/High/Critical),
`raised_by` Link User, `raised_by_name`, `contact_phone`, `status` Select (Open/Acknowledged/In Progress/Waiting on Institution/Resolved/Closed)
(permlevel 1), `assigned_to` Link User (permlevel 1), `response_due_by`, `resolution_due_by` (1 h / 4 h), `first_response_on`, `resolved_on`,
`response_sla_breached`, `resolution_sla_breached`, `escalation_level`, `waiting_since` (hidden; time waiting on the institution does not
count against resolution), `resolution_details` (permlevel 1), `ops_alert` Link Ops Alert (permlevel 2).
Every ticket opens an Ops Alert `support.ticket.<name>` (category SLA, source Support); outage tickets are Critical.
**Monthly Performance Report** (submittable) — autoname `format:MPR-{month}`; `month` (YYYY-MM), `from_date`, `to_date`,
`instance_label`, `prepared_on`, `sent_on`, `sent_to`, `uptime_percent`, `downtime_minutes`, `uptime_source`,
`unmonitored_minutes` (minutes no probe measured; counted as unavailable), `monitoring_coverage_percent`, `incidents` (count),
`critical_alerts`, `avg_cpu_percent`, `peak_cpu_percent`, `avg_memory_percent`, `peak_memory_percent`, `disk_used_percent`,
`security_events` Int, `malware_detections` Int, `waf_blocks` Int, `backups_succeeded`, `backups_failed`, `last_restore_test_on`,
`tickets_raised`, `tickets_resolved`, `sla_compliance_percent`, `active_students`, `portal_logins`, `online_payments_count`,
`online_payments_amount`, `incident_details` Table Monthly Performance Report Incident (`alert`, `title`, `severity`, `category`,
`first_seen`, `resolved_on`, `duration_minutes`, `is_outage`), `summary` Text Editor. Produced on the 1st at 07:00, submitted and
e-mailed as PDF to `report_recipients` by the 6th (TR 13); the health checks retry an unsent report hourly and raise
`reports.monthly.unsent` (Critical from the 4th). Availability prefers `uptime_external` samples (Uptime Kuma, Mumbai) over the
on-host `uptime_probe`.

### 4.8 Hostel Migration (`hostel360/hostel_migration`)

**Migration Run** — naming series `MIG-.YYYY.-.#####`; `title`, `status` Select (Draft/Running/Completed/Completed with Errors/Failed),
`dry_run` Check (1), `started_by`, `started_on`, `finished_on`, `source_path` (relative to the site's `private/migration/`),
`entity_filter`, `mapping_name`, `source_system`, `mapping_checksum`, `mapping` Code (JSON), totals (`total_source_rows`,
`total_created`, `total_updated`, `total_skipped`, `total_failed`, `attachments_verified`), `entities` Table Migration Run Entity
(`entity`, `target_doctype`, `source_file`, `status`, `source_rows`, `excluded`, `created`, `updated`, `skipped`, `failed`,
`target_count`, `source_checksum`, `duration_seconds`), `reconciliation_status` Select (Not Run/Matched/Mismatched),
`report_file` (XLSX), `report_markdown`, `report_json`, `reconciliation` Code, `log` Code. Desk actions: Start, Dry Run Again,
Migrate for Real.

**Migration Record Map** — autoname hash; `source_system`, `source_entity`, `source_id`, `map_key` (hidden, unique:
system + entity + legacy ID), `target_doctype`, `target_name`, `row_checksum` (SHA-256 of the mapped values), `source_row`,
`migration_run`, `last_synced_on`. Makes every run idempotent; kept per source system so two legacy applications can share IDs.

**Exit Export Log** — autoname `field:export_name`; `export_name`, `status` Select (Exported/Verified/Verification Failed), `site`,
`exported_on`, `exported_by`, `recorded_from` Select (Export/Verification), `include_files`, `encrypted`, `doctype_count`,
`entry_count`, `total_bytes`, `archive_path`, `directory_path`, `manifest_sha256`, `archive_sha256`, `verified_on`, `verified_by`,
`verified_path`, `verification_problems`, `acknowledgement_reference`, `purged_on`, `certificate_id`, `export_copy_deleted`.
Permissions: System Manager R/D/X, Hostel Administrator R/X, HyFib Operations R, Institution Auditor R. Holds no student or
parent data and is kept by the purge as the site's audit trail of the exit.

## 5. Service interfaces (Python)

Business rules live in DocType controllers and these service functions. The portal API calls
them; nothing else re-implements rules.

```python
# hostel360/utils/context.py (shared, implemented)
get_portal_identity(user=None) -> {kind, student, guardian, wards}
require_portal_identity(kind=None); assert_can_access_student(student, user=None)

# hostel360/hostel_people/services.py
get_student_summary(student: str) -> dict          # profile + current room + guardians (masked mobiles)
confirm_profile(student: str, updates: dict) -> None   # allowed keys: email, address_line1, address_line2, city, district, state, pincode, emergency_contact_name, emergency_contact_phone, blood_group
record_privacy_consent(doctype: str, name: str, version: str) -> None
ensure_portal_user(doctype: str, name: str) -> str   # creates/returns the Website User for a student or guardian
get_student_hostel(student: str) -> str | None       # current hostel, else last_hostel
get_student_block(student: str) -> str | None
get_staff_scope(user: str | None = None) -> dict | None      # None = unrestricted; {hostel: blocks or None}
staff_covers(user: str | None, hostel: str | None, block: str | None = None) -> bool
staff_covers_student(user: str | None, student: str) -> bool
get_scoped_staff_users(role: str, hostel: str | None, block: str | None = None) -> list[str]

# hostel360/hostel_fees/services.py
get_fee_summary(student: str) -> dict               # {outstanding, overdue, overdue_count, next_due_date, invoices:[...], payments:[...], payment_enabled}
online_payment_available(student: str, user: str | None = None, settings=None) -> bool
create_invoice(student, items, invoice_type, due_date, reference_doctype=None, reference_name=None, posting_date=None, remarks=None, submit=True) -> str
cancel_invoice(invoice: str) -> None
create_payment_order(student: str, invoices: list[str], user: str, ip_address: str | None = None) -> dict   # {transaction, gateway, amount, test_mode, checkout}
confirm_checkout(transaction: str, payload: dict) -> dict   # {status, payment, receipt_number, transaction, message?}
confirm_gateway_return(gateway_name: str, fields: dict, source: str = "checkout") -> frappe._dict   # {transaction, status, result}
get_receipt_pdf(payment: str) -> bytes
get_fee_structure_pdf(student: str) -> bytes

# hostel360/hostel_mess/services.py
get_mess_for_student(student: str) -> str | None
get_week_menu(mess: str, week_start: str | None = None, published_only: bool = True) -> dict
get_today_menu(student: str) -> dict | None
list_mess_bills(student: str) -> list[dict]
list_mess_reductions(student: str) -> list[dict]
apply_mess_reduction(student, from_date, to_date, reason, description=None, home_visit_leave=None, user=None) -> str
cancel_mess_reduction(name: str, user: str) -> None
decide_mess_reduction(name: str, approve: bool, remarks: str | None, user: str) -> None

# hostel360/student_services/services.py
list_leaves(student) -> list[dict]; apply_leave(student, data: dict, applied_by: str, user: str) -> str
parent_decision(leave: str, guardian: str, approve: bool, remarks: str | None) -> None
warden_decision(leave: str, approve: bool, remarks: str | None, user: str) -> None
cancel_leave(leave: str, user: str) -> None
list_pending_parent_approvals(guardian: str) -> list[dict]
list_grievances(student) -> list[dict]; get_grievance(name) -> dict   # includes public timeline
raise_grievance(student, data: dict, user: str, attachments: list[str] | None = None) -> str
add_grievance_comment(name, comment, user) -> None; reopen_grievance(name, reason, user) -> None
rate_grievance(name, rating: int, feedback: str | None, user) -> None
submit_feedback(student, data: dict) -> str
list_certificates(student) -> list[dict]; request_bonafide(student, purpose, purpose_details) -> str
get_bonafide_pdf(name) -> bytes

# hostel360/hostel_communication/dispatch.py
notify(event: str, recipients: list[dict], context: dict, reference_doctype: str | None = None,
       reference_name: str | None = None, channels: list[str] | None = None) -> None
# recipient dict: {"user"?: str, "mobile"?: str, "email"?: str, "name"?: str, "kind"?: str, "language"?: "en" | "ta"}
recipients_for_student(student, include_student=True, include_guardians=False) -> list[dict]
recipients_for_users(users: list[str]) -> list[dict]
recipients_for_approvers(role: str, hostel: str | None = None, block: str | None = None, student: str | None = None) -> list[dict]
        # role holders whose staff scope covers the hostel/block (or the student's), plus every warden and administrator
send_otp_message(mobile: str, otp: str, channel: str, timeout=None, language: str | None = None) -> bool   # synchronous, never logs the OTP
send_otp_email(email: str, otp: str, timeout=None, language: str | None = None, name: str | None = None) -> bool
        # synchronous, straight to the SMTP server (no Email Queue copy), never logs the OTP; refuses .invalid addresses

# hostel360/hostel_operations/alerts.py
raise_alert(key, title, severity, category, source="App", description="", details=None, is_outage=False) -> str
resolve_alert(key, notes="", source=None) -> bool
```

`raise_alert` and `resolve_alert` write in a short transaction of their own (a second
database connection, `hostel_operations.transaction.independent`), serialised per key by a
named lock, so an alert survives the failure of the request that raised it and never rolls
back the caller's work. They never raise, with one exception: when no second connection
could be opened they write inline, and a deadlock there (InnoDB has already rolled back the
caller's transaction) is re-raised as `frappe.QueryDeadlockError`. Wrappers in other
modules (`hostel_fees.alerting`, `hostel_communication.providers.base`, `security.files`)
re-raise it too.

`notify` is asynchronous (enqueued on the `short` queue after the caller's transaction
commits), template-driven, picks the recipient's language (English or Tamil, from
`User.language` or the recipient dict), honours channel enablement, writes Message Log,
and also creates a Frappe **Notification Log** for recipients with a user account (desk
bell and portal inbox). `notify` is a no-op while `frappe.flags.in_install`,
`frappe.flags.in_migrate`, `frappe.flags.in_import` or `frappe.flags.hostel360_migration`
is set. E-mail addresses on the `.invalid` TLD (placeholder addresses of portal users
without e-mail) are never sent to. Failed messages are retried with exponential back-off
every 15 minutes.

```python
# hostel360/hostel_communication/dispatch.py (template-free, synchronous; used by Operations alerts)
send_direct(channel: str, to: str, subject: str | None, body: str) -> bool   # channel: "Email" | "SMS" | "WhatsApp"
```

Alert deliveries are queued on `frappe.conf.hostel360_alert_queue` when the site config
names one (production: `alerts`, defined in `common_site_config.json` `workers.alerts` and
listed first by every worker), otherwise at the front of the `short` queue.

### Historical data (migration) contract
When `frappe.flags.hostel360_migration` is set, controllers must accept historical records: skip
notifications, skip "date must not be in the past" style checks, accept statuses and amounts supplied by
the migration (e.g. already-paid invoices, vacated allotments), and never call external gateways.

### Portal user accounts
Portal users are Website Users (role Hostel Student / Hostel Parent) created by
`ensure_portal_user`. Their login e-mail is the real e-mail when present, otherwise a placeholder
`<slug>@portal.hostel360.invalid`. `User.mobile_no` is NOT set (it is unique in Frappe and a student
may share a mobile number with a guardian); the mobile ↔ identity mapping lives in Hostel Student /
Hostel Guardian. A mobile number may belong to both a student and a guardian: `verify_otp` then
returns `{choose: ["student", "parent"]}` without consuming the OTP and the client repeats the call
with `kind`. A portal user may never hold a desk role (`auth.events.validate_user`).

## 6. Portal HTTP API

All endpoints are `POST` (reads may also be `GET`) under `/api/method/…`, return Frappe's
`{"message": …}` envelope, and require a logged-in Hostel Student or Hostel Parent except where
noted. For parents, `student` selects the ward; for students it is ignored. Session cookies
are `HttpOnly; Secure; SameSite=Lax`; mutating calls send `X-Frappe-CSRF-Token`. Writes are
rate limited per signed-in user (a campus shares public IP addresses). Full request and
response shapes: [API.md](API.md).

### Authentication — `hostel360.api.auth`
| Method | Guest | Request | Response |
|---|---|---|---|
| `send_otp` | yes | `mobile`, `channel` ("sms"\|"whatsapp"\|"email") | `{sent: true, masked: "98xxxxxx10", resend_after: 60, expires_in: 300}` — identical response for unknown numbers, for numbers without an e-mail address on record and for "email" while e-mail codes are off; `masked` is always the masked mobile |
| `verify_otp` | yes | `mobile`, `otp`, `kind?` | `{ok: true, kind: "student"\|"parent", redirect: "/hostel"}` and session cookie; or `{ok: false, choose: ["student","parent"]}` when the number belongs to both |
| `get_csrf_token` | no | – | `{csrf_token}` |
| `unlock_sign_in` | no (Hostel Administrator, Warden, Office Staff, System Manager) | `mobile` | `{ok: true}` — lifts the one-hour lock after repeated wrong codes |

Limits: 5 sends/hour/mobile, 60 s cooldown, 20 sends/hour/IP (trusted networks in Messaging Settings exempt),
5 wrong attempts invalidate the OTP, 10 failed verifications/hour lock the number for 1 hour, and a coarse
flood guard of 3,000 calls/hour/IP (100,000 for trusted networks). OTPs are random (secrets), stored only as
HMAC-SHA256 in Redis. Unknown, unvalidated and closed numbers get the same response, the same timing and the
same limits. The limits are per number whatever the channel.

E-mail codes (`otp_email_enabled`): the mobile number identifies the student or parent exactly as for SMS; the
code goes to the `email` on that Hostel Student / Hostel Guardian record (real addresses only, never a
`.invalid` placeholder). The first record with an address (student before parent) decides where it goes, and
the code then signs in only the records registered with that address (stored with the code as its allowed
kinds), so a student who shares a parent's phone cannot sign in as the parent from their own inbox. A number
with no address on record stores a code nobody receives and waits as long as a typical e-mail send (measured
apart from SMS). An e-mail that fails is not replaced by an SMS. While e-mail codes are off, "email" is treated
like any channel that is not allowed (the allowed mobile channels).

### Portal — `hostel360.api.portal`
| Method | Request | Response (message) |
|---|---|---|
| `get_session` | – | `{user, kind, display_name, mobile_masked, wards:[{student, full_name, register_number, photo, hostel, hostel_name, room, room_number, bed, bed_label, status, validation_status, payment_enabled}], validation_status, profile_confirmed, consent_required, privacy_notice_version, privacy_consent_version, unread_notifications, language, settings:{institution_name, institution_short_name, logo, office_phone, office_email, support_phone, support_email, payment_enabled, mess_reduction_min_days, mess_day_cutoff_hour, leave_requires_parent_approval, time_zone, upload:{max_bytes, extensions}}}` |
| `get_privacy_notice` (guest allowed, rate limited) | – | `{version, notice, accepted_version, accepted_on, consent_required}` |
| `accept_privacy_notice` | `version` | `{ok}` |
| `set_language` | `lang` ("en"\|"ta") | `{ok, language}` |
| `get_dashboard` | `student?` | `{student, student_name, student_status, restricted, room, fees:{outstanding, overdue, overdue_count, next_due_date, payment_enabled} \| null, today_menu, pending:{leaves, reductions, grievances, certificates, approvals?}, notifications:[…5], unread_notifications, unavailable:[…]}` |
| `get_profile` / `confirm_profile` / `download_photo` | `student?` / `updates` / `student?` | profile dict / `{ok}` / image |
| `get_fees` | `student?` | `{student, outstanding, overdue, overdue_count, next_due_date, invoices:[{name, invoice_type, academic_year, posting_date, due_date, total_amount, paid_amount, outstanding_amount, status, items:[…], payment_in_progress}], payments:[{name, payment_date, amount, mode_of_payment, reference_no}], payment_enabled, payment_test_mode}` |
| `create_payment` | `invoices` (list), `student?` | `{transaction, gateway:"razorpay"\|"payu", amount, test_mode, checkout}` — Razorpay: `{key, order_id, amount, currency, name, description, prefill:{name,email,contact}, notes, image?, timeout?}`; PayU: `{action_url, method:"POST", fields:{…, hash}}` |
| `verify_payment` | `transaction`, `razorpay_payment_id`, `razorpay_order_id`, `razorpay_signature` | `{status, payment, receipt_number, transaction, message?, test_mode}` |
| `get_payment_status` | `transaction` | same shape as `verify_payment`; settles an open redirect (PayU) payment with the gateway |
| `download_receipt` | `payment` | PDF download |
| `download_fee_structure` | `student?` | PDF download |
| `get_menu` | `week_start?`, `student?` | `{mess, week_start, week_end, status, published, notes, days:[{date, day, is_today, meals:[{meal, dishes, is_special}]}]}` |
| `get_mess_bills` | `student?` | `[{name, mess, from_date, to_date, enrolled_days, reduction_days, chargeable_days, daily_rate, mess_charge, extras_amount, rounding_adjustment, total_amount, fee_invoice, invoice_status, outstanding_amount, due_date}]` |
| `list_mess_reductions` / `apply_mess_reduction` / `cancel_mess_reduction` | `student?` / `from_date, to_date, reason, description, student?` / `name` | list / `{name}` / `{ok}` |
| `list_leaves` / `apply_leave` / `cancel_leave` | `student?` / `from_datetime, to_datetime, destination, reason, contact_during_leave, apply_mess_reduction, student?` / `name` | list / `{name, status}` / `{ok, status}` |
| `pending_leave_approvals` (parent) | – | list of leaves awaiting this parent |
| `decide_leave` (parent) | `name`, `action` ("approve"\|"reject"), `remarks?` | `{ok, status}` |
| `list_grievances` / `get_grievance` / `raise_grievance` | `student?` / `name` / `category, subject, description, priority, is_confidential, attachments?, student?` | list / detail with `attachments:[{name, file_name, file_size, url}]` and `timeline:[{id, on, by, is_staff, message, display_message, status_change, is_mine}]` / `{name}` |
| `comment_grievance` / `reopen_grievance` / `rate_grievance` | `name, comment` / `name, reason` / `name, rating(1-5), feedback?` | `{ok}` |
| `upload_attachment` | multipart `file`, `purpose` ("grievance"), `student?` | `{file_url, file_name, file_size}` (private, scanned) |
| `download_grievance_attachment` | `name`, `file` | the file (GET) |
| `submit_feedback` | `feedback_type, overall, food_quality?, cleanliness?, staff_behaviour?, facilities?, comments?, is_anonymous?, period?` (student only) | `{name}` |
| `list_certificates` / `request_bonafide` / `download_bonafide` | `student?` / `purpose, purpose_details, student?` / `name` | list / `{name}` / PDF |
| `list_notifications` / `mark_notifications_read` | `limit?, start?` / `names?` | `[{name, subject, message, created, read, link, student}]` / `{ok}` |

Errors use Frappe exceptions (`ValidationError` 417, `PermissionError` 403, `DoesNotExistError` 404,
rate limit 429) with a translated, user-safe message. Portal-specific refusals (all `PermissionError`
subclasses, the class name is in `exc_type`): `PortalAccessClosed` (the record may not use the portal
now; the SPA signs out and shows `/access-closed`), `PortalValidationPending`, `PortalStudentInactive`,
`PortalConsentRequired` (accept the current privacy notice first).

### Payments — `hostel360.api.payments`
| Method | Guest | Purpose |
|---|---|---|
| `razorpay_webhook` | yes (POST) | Verifies `X-Razorpay-Signature` over the raw body; handles `payment.captured` and `order.paid` (receipt), `payment.failed` (attempt recorded; the transaction stays Pending until it expires because the payer may retry on the same order), `payment.authorized` (pending; reconciliation captures), `refund.created` / `refund.processed`; idempotent |
| `payu_return` | yes (POST) | PayU's `surl`/`furl`: verifies the response hash, reads the payment back from PayU's verify API, then 303-redirects to `/hostel/fees?payment=<transaction>&status=success\|failed\|pending` |
| `payu_webhook` | yes (POST) | PayU server-to-server notification, same verification as the return |

### Operations — `hostel360.api.ops` and `hostel360.api.health`
| Method | Auth | Purpose |
|---|---|---|
| `health.liveness` | guest | `{status:"ok"}` — load balancer health check, no DB access |
| `health.readiness` | guest | `{status:"ok"\|"degraded"}` (503 when degraded) — DB + Redis ping, no details |
| `health.status` | HyFib Operations / Auditor / System Manager | detailed component status, open alert counts, last backup, versions |
| `ops.ingest` | HMAC (`X-H360-Timestamp`, `X-H360-Signature` = hex HMAC-SHA256 of `timestamp.body` with `ingest_secret`, ±300 s) | host agent: `{type:"alert"\|"resolve"\|"metrics"\|"backup"\|"heartbeat", message_id?, …}` |
| `ops.oci_notification` | `token` in query + OCI confirmation handshake | OCI Monitoring alarm → Ops Alert (`?outage=1` marks it an outage) |
| `ops.acknowledge` / `ops.resolve` / `ops.suppress` | HyFib Operations / System Manager | alert lifecycle from desk or e-mail links |
| `ops.summary` | HyFib Operations / System Manager | open alert counts for the desk indicator |

`ops.ingest` status codes tell the agent what to do: 2xx = delivered; **409** = an earlier attempt
of this message (same signature, or same `message_id`) was really committed, treat as delivered;
**422** / **413** / **400** = permanent, drop the message; **401** = signature or clock problem;
**503** or any other 5xx = keep it in the spool and retry. `message_id` is 8–100 characters of
`[A-Za-z0-9._:-]`, created once per message and kept on every retry. `ops.oci_notification`
answers **503** when an alarm could not be recorded, so OCI Notifications delivers it again.

## 7. Student/parent portal (SPA)

Routes under `/hostel`: `login`, `access-closed`, `` (home), `profile`, `more`, `fees`, `fees/pay/:invoice?`, `receipts`, `menu`,
`mess-bills`, `mess-reduction`, `mess-reduction/new`, `leave`, `leave/new`, `leave/approvals` (parent), `grievances`,
`grievances/new`, `grievances/:name`, `feedback` (student), `certificates`, `notifications`, `privacy`, `help`,
`confirm-details` (student, first sign-in). The router guard enforces sign-in → privacy consent → confirm details →
role → validation, in that order.
Mobile-first, bottom navigation, offline shell, English/Tamil (the language switch also calls `set_language` so server
messages and notifications follow), WCAG 2.1 AA, Razorpay Checkout overlay or PayU hosted form post for payments,
CSP-compatible (no inline scripts). Build output: `hostel360/public/frontend/` and `hostel360/www/hostel.html`.

`hostel360/www/hostel.html` is written by the Vite build from `frontend/index.html`; its page
context comes from `hostel360/www/hostel.py`, which provides `csrf_token` and `boot_json` and
sends desk users to the desk. The template carries them without inline script:

```html
<meta name="csrf-token" content="{{ csrf_token }}">
<script type="application/json" id="h360-boot">{{ boot_json }}</script>
```

`boot_json` (already JSON-escaped for HTML) = `{site_name, institution_name, institution_short_name,
logo, office_phone, office_email, support_phone, support_email, is_logged_in, kind, lang, otp_email}`;
`otp_email` (site-wide, says nothing about any number) makes the sign-in page offer E-mail as a third channel. The SPA
reads both at start-up and calls `hostel360.api.portal.get_session` for everything else.

## 8. Security controls

Layer 1 (network): OCI NSGs (443 only via LB; SSH only through OCI Bastion from listed office addresses), DDoS protection,
private subnet without public IP, IMDSv2 only, optional OCI WAF.
Layer 2 (web): ModSecurity + OWASP CRS 4 (paranoia 1, anomaly threshold 5) with path-scoped Frappe exclusions, TLS 1.2/1.3,
HSTS, CSP (strict for `/hostel`), rate limits on sign-in endpoints (campus egress addresses exempt), fail2ban,
ClamAV scan-on-upload + maldet realtime monitor, host firewall (ufw, DOCKER-USER).
Layer 3 (application): RBAC with hostel/block scoping, 2FA for every desk role (and System Manager/Administrator),
OTP for portal, validation before portal sign-in, rate limits, password policy (score ≥ 3), lockout after 5 failures,
8-hour sessions, audit trail (Version, Activity Log, Access Log, security log), upload policy (extension allow-list,
magic-byte check, 5 MB portal / 10 MB staff, portal uploads private and only through `upload_attachment`, enforced before
Frappe writes any bytes by the `File` override `security.files.HostelFile` and the `before_request` hook
`security.files.block_portal_generic_upload`), encrypted secrets (Password fields), no tracebacks to users.
Sharing restrictions for Hostel360 records (no "share with everyone", no shares to accounts without desk access) are
enforced by the registered DocShare `validate` hook `security.sharing.validate_doc_share`; `remove_unsafe_shares` removes
older shares on every install and migrate. `block_portal_generic_upload` judges the user Frappe's `validate_auth` resolves,
so an `Authorization` header never exempts a request; abandoned chunk files are removed hourly.
Data: TLS in transit; AES-256 at rest (OCI block/object encryption, restic repository encryption, Frappe backup encryption,
AES-256-GCM for encrypted exit exports).
Logs: retained 180 days in India (journald, rsyslog, WAF/ClamAV/MariaDB logs, OCI Logging; Frappe Error/API Request/Integration
logs 180 days, Activity/Access Logs 365 days, enforced on every migrate); time synchronised to NIC/NPL NTP servers
(CERT-In directions 28 Apr 2022).
Incidents: Security Incident DocType with the six-hour CERT-In clock; report to CERT-In and the institution within 6 hours
(`deploy/monitoring/security-incident-runbook.md`).
Privacy (DPDP Act 2023): purpose-limited collection, privacy notice readable before sign-in and consent capture before any
personal data is shown, masked identifiers in logs, data export/erasure via `user_data_fields`, personal-data breach handling
through ACGCET as Data Fiduciary.

## 9. Integration contract — hooks

`hostel360/hooks.py` is authoritative. Scheduled jobs and their owners:

| Hook target | Module |
|---|---|
| `hostel_operations.escalation.run_escalations` (every minute) | Operations |
| `hostel_operations.health.run_health_checks` (5 min; also retries an unsent monthly report hourly) | Operations |
| `hostel_operations.support.check_ticket_slas` (5 min) | Operations |
| `hostel_fees.reconciliation.reconcile_pending_transactions` (15 min), `daily_reconciliation` (23:30) | Fees |
| `hostel_communication.dispatch.retry_failed_messages` (15 min) | Communication |
| `hostel_fees.reminders.send_fee_reminders` (08:00) | Fees |
| `hostel_operations.reports.generate_monthly_performance_report` (1st, 07:00) | Operations |
| `student_services.grievance_tasks.escalate_overdue_grievances` (hourly) | Student Services |
| `student_services.leave_tasks.mark_overdue_returns` (hourly) | Student Services |
| `hostel_fees.tasks.mark_overdue_invoices` (daily) | Fees |
| `hostel_people.tasks.close_ended_allotments` (daily) | People |
| `hostel_operations.maintenance.purge_old_metric_samples` (daily; also purges Ops Ingest Receipts older than 3 days) | Operations |
| `api.portal.delete_stale_uploads` (daily; portal uploads never attached within 24 h) | Portal |
| `hostel_mess.tasks.send_low_stock_digest` (weekly) | Mess |

Other hooks: `permission_query_conditions` and `has_permission` (§3 scoping table);
`override_doctype_class = {"File": "hostel360.security.files.HostelFile"}`;
`before_request = ["hostel360.security.files.block_portal_generic_upload"]`;
`doc_events`: File `before_insert` (`security.files.validate_upload`) and `after_insert`
(`security.files.enqueue_malware_scan`), User `validate` (`auth.events.validate_user`), Comment
`after_insert` (`hostel_operations.support.on_comment`: a HyFib reply counts as first response),
Hostel Fee Invoice `on_submit` (`hostel_mess.billing.sync_amended_invoice`);
`on_login`, `on_session_creation`, `get_website_user_home_page` (`auth.events`);
`default_log_clearing_doctypes` (CERT-In retention); `user_data_fields` (DPDP export/erasure).

Every module also implements an idempotent `hostel360.<module>.setup.install_defaults()`,
called from `after_install` and `after_migrate`.

## 10. Non-functional targets

1,500 concurrent users (150 req/s peak, p95 < 800 ms), 99.9 % monthly uptime, RPO 24 h, RTO 4 h,
backups daily (30 d) / weekly (12 w) / monthly (12 m), monthly restore test, logs 180 days.

Go-live requirements that only the institution can supply: the campus egress (public) addresses of the hostel and
college gateway, so that students on campus Wi-Fi are not rate-limited as one client
(`h360_campus_egress_cidrs`, Messaging Settings > Trusted Networks); report recipients; DLT and WhatsApp template approvals;
the merchant account keys. The full list is the go-live checklist in `deploy/README.md` §3.7.

Evidence: capacity with the k6 load test in `tests/load/` ([LOAD-TESTING.md](LOAD-TESTING.md)); edge and server configuration
with the regression checks in `deploy/tests/`; application behaviour with the server and portal test suites run in CI
([DEVELOPMENT.md](DEVELOPMENT.md)).

## 11. Revision history

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-10-15 | First issue. |
| 1.1 | 2026-10-15 | Aligned with the delivered implementation: Hostel Security role and gate-pass page; block scoping, guardian scoping and the row-level permission hooks; `last_hostel`; validation-before-sign-in rule; PayU gateway, test payers and the extended gateway contract; new fee, mess, student-services and operations fields; Security Incident, Ops Ingest Receipt, Exit Export Log, Migration Run/Entity/Record Map (with `source_system`); Message Log permlevels; Tamil templates and new events (`leave_checked_out`, `leave_returned`, `bonafide_requested`); new portal endpoints and response fields; `ops.ingest` status codes and `message_id`; the `alerts` queue; upload limits 5 MB portal / 10 MB staff; bench commands; revised hooks table. |
| 1.3 | 2026-10-16 | Desk homes and areas per role (§3): `/staff` landing, Hostel Admin workspace limited to the roles that run the hostel, Gate workspace and module "Hostel Gate" for Hostel Security, dock narrowed to each role's areas, Hostel Security on the apps screen. |
| 1.2 | 2026-10-16 | Sign-in codes by e-mail (clause 3.2): Messaging Settings `otp_email_enabled`; `send_otp` channel "email" with the e-mail rules in §6; Message Templates `otp_login-Email` and `otp_login-Email-ta`; `dispatch.send_otp_email`; boot `otp_email`. |
