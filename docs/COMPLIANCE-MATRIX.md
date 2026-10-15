# Compliance matrix

Every clause of HyFib's signed technical compliance statement (HYFIB/ACGCET/HMS/2026/08,
tender ACGCET/4903/B1/2026, dated 14.10.2026) mapped to what implements it, where, and the
test or evidence that shows it. Paths are relative to the repository root; "tests" are run
by CI (`.github/workflows/ci.yml`) on every change.

Status:
**Implemented** — in the software and covered by tests;
**Configured** — met by the infrastructure or configuration in `deploy/`;
**Operational** — met by a procedure, with the evidence produced while running the service;
**Partial** — part of the statement is not yet met (explained in the row);
**Commercial** — a bid or contract term, not a software feature.

Evidence marked *(go-live)* or *(monthly)* is produced on the production installation:
the go-live checklist (`deploy/README.md` §3.7), Ops records, and the Monthly Performance
Report.

## A. Technical specification, item 1 — Cloud based hostel management software (4 modules)

### Admin module

| Clause | Requirement | Status | How Hostel360 meets it | Code / DocTypes | Test / evidence |
|---|---|---|---|---|---|
| 1.1 | Deputy Warden Registration | Implemented | Hostel Staff with designation *Deputy Warden*, photo, hostel and optional block assignments; saving with *Create User* makes a desk login with the Deputy Warden role, two-factor sign-in and User Permissions for the assigned hostels; a block assignment limits the person to that block's students | Hostel Staff, Hostel Staff Assignment; `hostel_people/doctype/hostel_staff/`, `hostel_people/permissions.py`, `setup/roles.py` | `hostel_people/doctype/hostel_staff/test_hostel_staff.py`, `hostel_people/tests/test_scoping.py` |
| 1.2 | Staff Registration | Implemented | Hostel Staff for every designation (Administrator, Warden, Deputy Warden, Office Staff, Mess Manager, Mess Staff, Security, Housekeeping, Other) with roles and access rights from the designation; welcome e-mail; deactivating disables the login | Hostel Staff; `DESIGNATION_ROLES`, `HOSTEL_SCOPED_DESIGNATIONS` | `test_hostel_staff.py`, `hostel_operations/tests/test_permissions.py` |
| 1.3 | Student Registration | Implemented | Student master with academic, hostel, contact, medical (restricted) details, photo and guardian links; bulk import from Excel/CSV with a downloadable template, row-by-row validation, background import and a "rows to fix" file; validation against the college register | Hostel Student, Hostel Guardian, Hostel Student Guardian, Student Bulk Import | `test_hostel_student.py`, `test_hostel_guardian.py`, `test_student_bulk_import.py` |
| 1.4 | Hostel Registration | Implemented | Hostels, blocks, floors, rooms and beds with capacity, room types, facilities and status; beds created from room capacity; live occupancy counters | Hostel, Hostel Block, Hostel Floor, Hostel Room, Hostel Bed, Room Facility; `hostel_setup/occupancy.py` | `hostel_setup/doctype/*/test_*.py` |
| 1.5 | Fees Collection through payment portal | Implemented | Fee heads and structures per hostel/year/programme; **Publish Dues** creates invoices; online payment through Razorpay or PayU with collections settled directly to ACGCET's merchant account; receipts issued automatically after the gateway confirms; webhooks and 15-minute + daily reconciliation; counter receipts (cash, UPI, DD, cheque, transfer) | Hostel Fee Head/Structure/Invoice, Hostel Payment, Hostel Payment Transaction, Hostel Payment Settings; `hostel_fees/services.py`, `gateways/`, `transactions.py`, `reconciliation.py`, `webhooks.py`, `api/payments.py` | `test_hostel_fee_structure.py`, `test_hostel_fee_invoice.py`, `test_hostel_payment.py`, `test_hostel_payment_transaction.py`, `hostel_fees/tests/test_payu.py`, `test_payment_concurrency.py`; one real payment end to end *(go-live, checklist item 11)* |
| 1.6 | Room Allotment | Implemented | Allot, transfer and vacate with gender and capacity checks, row locks against double allotment, full history; visual **Room Board** (hostel → block → floor → room → bed) with allot/transfer/vacate/reserve | Room Allotment; page `room-board`; `hostel_people/doctype/room_allotment/`, `hostel_people/page/room_board/` | `test_room_allotment.py`, `hostel_people/tests/test_room_board.py` |
| 1.7 | Report Generation | Implemented | 18 standard reports with Excel/PDF export: Hostel Occupancy, Student Register, Room Allotment History, Fee Collection, Fee Dues, Daily Collection Summary, Gateway Reconciliation, Mess Stock Balance, Mess Consumption, Mess Cost Summary, Mess Reduction Register, Leave Register, Students On Leave, Grievance Summary, Feedback Summary, Bonafide Register, Alert History, Uptime and Incidents; workspace dashboards with number cards and charts. Further reports from the To-Be study are delivered at no cost (TR 6) | `*/report/*`, `*/number_card/*`, `*/dashboard_chart/*`, `*/workspace/*` | `hostel_fees/tests/test_fee_reports.py`, `student_services/report/test_student_services_reports.py`, `hostel_setup/dashboard_chart_source/hostel_occupancy/test_hostel_occupancy.py`, `hostel_setup/workspace/test_hostel360_desk.py` |

### Mess module

| Clause | Requirement | Status | How Hostel360 meets it | Code / DocTypes | Test / evidence |
|---|---|---|---|---|---|
| 2.1 | Grocery stock entry | Implemented | Receipts, issues, adjustments and returns with supplier, quantity and rate; moving-average valuation per mess and item; running balance; issues cannot exceed stock; back-dated entries revalued; published periods closed | Mess Stock Entry, Mess Stock Ledger Entry, Mess Item, Mess Supplier; `hostel_mess/stock.py` | `test_mess_stock_entry.py`, `test_mess_stock_ledger_entry.py`, `test_mess_item.py` |
| 2.2 | Daily menu | Implemented | Weekly menu planner (breakfast, lunch, snacks, dinner, specials), copy previous week, **Publish** notifies residents; today's and the week's menu in the portal | Mess Menu, Mess Menu Item; `hostel_mess/services.get_week_menu` | `test_mess_menu.py`, `api/test_portal.py` |
| 2.3 | Mess reduction approval | Implemented | Approval by the Deputy Warden or Mess Manager as configured (and the Warden/Administrator), limited to the students the approver covers; rules for minimum days, advance notice, overlap and a monthly cap; one **Approvals** inbox | Mess Reduction Application, Mess Settings; page `approvals` | `test_mess_reduction_application.py`, `student_services/page/approvals/test_approvals.py` |
| 2.4 | Demand of commodities | Implemented | Indents with items below reorder level suggested, linked to the week's menu for the approver; approval by Mess Manager/Warden/Administrator with separation of duties and a warden threshold; ordered and received (drafts the stock receipt). Recipe-based quantity suggestions are not part of the delivered scope | Mess Indent, Mess Indent Item | `test_mess_indent.py` |
| 2.5 | Consumption Reports (Daily/Monthly/Yearly) | Implemented | Item-wise consumption with Daily, Monthly or Yearly granularity, stock balance on any date, cost summary per bill run | reports Mess Consumption, Mess Stock Balance, Mess Cost Summary | report tests run in CI; Excel/PDF export from the report view |
| 2.6 | Mess bill generation | Implemented | Monthly bill run on the dividing system: (issues + expenses − credits) ÷ total chargeable student-days; approved reductions and unbilled extras applied automatically; bills become fee invoices (Mess Charges, Mess Extras) payable online | Mess Bill Run, Mess Bill, Mess Expense, Mess Extra Charge; `hostel_mess/billing.py` | `test_mess_bill_run.py`, `test_mess_bill.py`, `test_mess_expense.py`, `test_mess_extra_charge.py` |

### Student module

| Clause | Requirement | Status | How Hostel360 meets it | Code / DocTypes | Test / evidence |
|---|---|---|---|---|---|
| 3.1 | Validation of students | Implemented | The office validates each registration against the college register (**Validate** / **Reject** with reason); only validated students who have not left can sign in, checked again on every portal call | Hostel Student `validation_status`; `auth/events.portal_login_blocked_reason`, `auth/otp.student_may_sign_in`, `api/portal.py` | `test_hostel_student.py`, `auth/test_events.py`, `auth/test_otp.py`, `api/test_portal.py` |
| 3.2 | Login with OTP | Implemented | One-time code (6 digits, 5 minutes, stored only as an HMAC, rate limited) for the registered mobile number, sent by **SMS or WhatsApp** to that number or, once *Allow E-mail Sign-in Codes* is ticked in Messaging Settings, by **e-mail** to the address the office has on the student or guardian record (never a placeholder portal address). The mobile number identifies the person on every channel; an e-mailed code signs in only the records registered with that address; it goes straight to the SMTP server inside the request (no Email Queue copy of the code, short timeout, circuit breaker). Unknown numbers, numbers without an e-mail address and requests while e-mail is off get the same response, timing and limits | `auth/otp.py` (`delivery_route`, `email_recipient`), `api/auth.py`, `hostel_communication/dispatch.send_otp_email`, `hostel_communication/providers/email.send_email_now`, Messaging Settings `otp_channels`, `otp_email_enabled`; Message Templates `otp_login-Email`, `otp_login-Email-ta`; `www/hostel.py` boot `otp_email`; portal `LoginView.vue` | `auth/test_otp.py` (`TestEmailCodes`, `TestEmailCodesWithRecords`), `hostel_communication/test_dispatch.py` (`TestEmailedSignInCodes`), `hostel_communication/providers/test_providers.py` (`TestEmailNow`), `api/test_portal.py`, `frontend/tests/login-channels.spec.ts`, `frontend/tests/otp-input.spec.ts` |
| 3.3 | Profile page | Implemented | Personal, academic, hostel and room details, guardians with masked numbers; confirm-details on first sign-in with editable contact fields; photo | `api/portal.get_profile`, `confirm_profile`, `download_photo`; `ProfileView.vue`, `ConfirmDetailsView.vue` | `api/test_portal.py`, `hostel_people/tests/test_services.py` |
| 3.4 | Fees Due | Implemented | Outstanding, overdue and next due date; invoices with items; pay online; receipts downloadable as PDF | `get_fees`, `create_payment`, `verify_payment`, `get_payment_status`, `download_receipt`; `FeesView.vue`, `PayView.vue`, `ReceiptsView.vue` | `api/test_portal.py`, `frontend/tests/portal-views.spec.ts`, `invoice-list.spec.ts` |
| 3.5 | Daily Menu | Implemented | Today's menu on the home screen and the week's menu | `get_dashboard.today_menu`, `get_menu`; `HomeView.vue`, `MenuView.vue` | `api/test_portal.py` |
| 3.6 | Mess Reduction application | Implemented | Online application with status tracking and cancellation; automatic application from an approved home-visit leave | `apply_mess_reduction`, `list_mess_reductions`, `cancel_mess_reduction`; `MessReduction*View.vue` | `test_mess_reduction_application.py`, `api/test_portal.py` |
| 3.7 | Leave application for home visit | Implemented | Apply online; parent approval, then warden approval, with notifications at each step; gate check-out and return; overdue flagged | Home Visit Leave; `apply_leave`, `cancel_leave`; page `gate-pass`; `LeaveNewView.vue` | `test_home_visit_leave.py`, `student_services/page/gate_pass/test_gate_pass.py`, `frontend/tests/leave-form.spec.ts` |
| 3.8 | Feedback/Grievances | Implemented | Raise grievances with photos, follow the timeline, comment, reopen within 7 days, rate the resolution; SLA with hourly escalation to the warden; ragging/harassment always confidential and escalated at once; monthly feedback forms (optionally anonymous) | Hostel Grievance, Hostel Grievance Update, Hostel Feedback; `student_services/grievance_tasks.py` | `test_hostel_grievance.py`, `test_hostel_feedback.py`, `security/test_files.py` |
| 3.9 | Bonafide Certificate (Report) | Implemented | Request online; approval by the warden; certificate number, validity and a QR code; download as PDF; public verification page | Bonafide Certificate Request; print format *Bonafide Certificate*; `/verify-certificate` | `test_bonafide_certificate_request.py` |
| 3.10 | Fees Structure (Report) | Implemented | The fee structure that applies to the student, downloadable as PDF | `download_fee_structure`; print format *Hostel Fee Structure*; `hostel_fees/services.get_applicable_fee_structure` | `hostel_fees/tests/test_fee_reports.py` |

### Parents module

| Clause | Requirement | Status | How Hostel360 meets it | Code / DocTypes | Test / evidence |
|---|---|---|---|---|---|
| 4.1 | Validation of parents | Implemented | A guardian signs in with the registered mobile number and only while linked to at least one validated resident; rejected guardians are closed | Hostel Guardian; `auth/otp.guardian_has_portal_ward`, `auth/events.py` | `test_hostel_guardian.py`, `auth/test_events.py`, `auth/test_otp.py` |
| 4.2 | Login with OTP | Implemented | One-time code for the registered mobile by SMS or WhatsApp, or by e-mail to the guardian's address on record when e-mail codes are allowed (as 3.2); a number shared with a student asks "who is signing in?", and an e-mailed code signs in only the records registered with that address | `auth/otp.py`, `api/auth.verify_otp` (`choose`) | `auth/test_otp.py` |
| 4.3 | Leave application | Implemented | View the ward's leave and apply on their behalf (skips the parent step) | `list_leaves`, `apply_leave` (parent) | `test_home_visit_leave.py`, `api/test_portal.py` |
| 4.4 | Leave approval | Implemented | Approve or reject the ward's leave online with remarks; only guardians marked *can approve leave* | `pending_leave_approvals`, `decide_leave`; `LeaveApprovalsView.vue` | `test_home_visit_leave.py`, `api/test_portal.py` |
| 4.5 | Mess bill due | Implemented | Mess bills with the daily rate and days, dues and online payment | `get_mess_bills`, `get_fees`; `MessBillsView.vue` | `test_mess_bill.py`, `api/test_portal.py` |

### Notes

| Clause | Requirement | Status | How Hostel360 meets it | Code / DocTypes | Test / evidence |
|---|---|---|---|---|---|
| 5.1 | Implemented, installed and fully integrated with the institution's official hostel website | Implemented, Configured | Portal on a sub-domain of the hostel website; ready-made Student/Parent/Staff login buttons (script embed, PHP include or static HTML), `/integration/` pages served by PHP-FPM, CORS for the website's origin; `/portal`, `/student`, `/parent` redirect to the portal | `deploy/integration/website/`, `hostel360/public/embed/`, `deploy/php-fpm/`, `hooks.py` (`website_redirects`) | `deploy/tests/waf/run.sh`; links live on the website *(go-live)* |
| 5.2 | Necessary training to all staff members | Operational | Initial and refresher sessions per role, manuals and quick guides | [TRAINING-PLAN.md](TRAINING-PLAN.md), [user manuals](user-manual/README.md) | Attendance records *(go-live, +30 days)* |

## B. Technical specification, item 2 — Cloud server configuration (OCI, 4 years)

| Clause | Requirement | Status | How it is met | Where | Evidence |
|---|---|---|---|---|---|
| 6.1 | 6 CPU cores | Configured (exceeds) | 8 OCPU Ampere A1 | `deploy/terraform/oci/variables.tf` `instance_ocpus` | `terraform output`, OCI console *(go-live)* |
| 6.2 | 12 GB RAM | Configured (exceeds) | 16 GB | `instance_memory_gbs` | same |
| 6.3 | 300 GB NVMe storage | Configured (exceeds) | 400 GB NVMe-backed block volume, Balanced, expandable at no cost | `data_volume_size_gbs`, `storage.tf` | same |
| 6.4 | 4,000,000 inodes | Configured | ext4 with about 26 million inodes; Ansible refuses fewer than 4,000,000 | `deploy/ansible/roles/storage` | Ansible run; `df -i` in the host agent's metrics |
| 6.5 | 300 PHP workers | Configured | PHP-FPM `pm = ondemand`, `pm.max_children = 300` | `deploy/php-fpm/` | pool configuration |
| 6.6 | Location: India | Configured | Hyderabad (server), Mumbai (backups) | `region`, `backup_region` | `terraform output` |
| 6.7 | Transaction units 100 × 10,000 | Configured | No per-transaction limit or charge; sized for at least 10 lakh transactions | architecture | load test ([LOAD-TESTING.md](LOAD-TESTING.md)) |
| 6.8 | Daily / weekly / monthly automated backup (DB / upload files) | Configured, Operational | restic daily (30), weekly (12), monthly (12) of databases and files to OCI Object Storage Mumbai, AES-256; OCI volume backups; reported as Ops Backup Records | `deploy/host/bin/h360-backup`, `storage.tf`, `objectstorage.tf` | Ops Backup Records; Monthly Performance Report *(monthly)*; [BACKUP-AND-DR.md](BACKUP-AND-DR.md) |
| 6.9 | SSL & site security protection | Configured | TLS 1.2/1.3 at the LB, HTTPS only with HSTS, ModSecurity + OWASP CRS 4, security headers, rate limits, fail2ban | `lb.tf`, `deploy/waf/` | `deploy/tests/waf/run.sh`; `security.tls.expiry` health check |
| 6.10 | Malware protection | Implemented, Configured | ClamAV scan-on-upload with automatic removal; Linux Malware Detect realtime monitor and daily scan with quarantine | `hostel360/security/files.py`, `deploy/clamav/`, `deploy/ansible/roles/malware` | `security/test_files.py`; malware detections in the Monthly Performance Report |
| 6.11 | High-speed bandwidth with 99.9 % uptime | Configured, Operational | OCI Flexible LB 10–100 Mbps, raised at no cost if needed; uptime measured every minute on the VM and from Mumbai | `lb.tf`, `h360-uptime-probe`, `deploy/monitoring/` | Monthly Performance Report `uptime_percent` *(monthly)* |
| 6.12 | Access sharing facility | Implemented, Configured | Institution Auditor desk role: dashboards, reports, Operations Status, alert history, read-only; ACGCET backup readers group on the bucket | `setup/roles.py`, page `ops-status`, `iam.tf` | `hostel_operations/tests/test_permissions.py` |
| 6.13 | Database & server maintenance | Operational | Unattended security updates, health checks every 5 minutes, MariaDB tuned and monitored, migrations with rollback (`h360-deploy`), routine tasks | `deploy/ansible/roles/common`, `hostel_operations/health.py`, `deploy/host/bin/h360-deploy` | [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) §7; Ops records |
| 6.14 | Annual technical support and maintenance | Operational | 24×7×365 helpdesk, SLAs, escalation, support tickets | Support Ticket, Ops Alert Settings escalation | `test_support_ticket.py`; ticket SLA compliance in the report *(monthly)* |

## C. Terms and conditions for e-tender

| Clause | Requirement | Status | How it is met |
|---|---|---|---|
| T&C 1 | Manufacturers and authorised dealers are eligible | Commercial | HyFib develops and owns HyFib Hostel360 |
| T&C 2 | Manufacturing licence or dealership certificate | Commercial | OEM certificate and incorporation documents in the bid |
| T&C 3–4 | Online submission through tntenders.gov.in | Commercial | Submitted online with a Class 3 DSC |
| T&C 5 | Documents on letterhead, signed and stamped | Commercial | Bid documents |
| T&C 6 | PAN and GST copies | Commercial | Bid document 6 |
| T&C 7 | Dealer authorisation from the manufacturer | Commercial (not applicable) | HyFib is the manufacturer |
| T&C 8 | Quantity approximate; tender may be amended | Commercial | Accepted |
| T&C 9 | Service/support branch in Tamil Nadu; no conditional offer | Operational | Chennai office; helpdesk and onsite engineer (TR 17) |
| T&C 10 | Ex-godown charges; no advance payment | Commercial | Cloud delivery |
| T&C 11–12 | GST percentage; prices in INR | Commercial | BoQ |
| T&C 13 | Ready for implementation within 30 working days of the purchase order | Operational | Implementation plan (bid document 11 §1); repeatable deployment (`deploy/`), scripted migration, demo data for UAT |
| T&C 14 | Brochures, architecture documents, user manuals, technical specifications, feature documentation | Implemented | This `docs/` folder: [ARCHITECTURE.md](ARCHITECTURE.md), [SOLUTION-DESIGN.md](SOLUTION-DESIGN.md), [user manuals](user-manual/README.md), [DEPLOYMENT.md](DEPLOYMENT.md), the README |
| T&C 15–17 | EMD | Commercial | Paid online |
| T&C 18 | Offer valid 180 days; support, bug fixes, patches, updates and maintenance at no additional cost | Operational | Releases through CI and `h360-deploy`; [CHANGELOG.md](../CHANGELOG.md); support model in the runbook |
| T&C 19–20 | Bills in triplicate; GST shown separately | Commercial | — |
| T&C 21–24 | Institute's rights; Karaikudi jurisdiction; 2 % TDS | Commercial | Accepted |
| T&C 25 | Delivered and commissioned at ACGCET Hostels, Karaikudi | Operational | Go-live checklist signed off (`deploy/README.md` §3.7) |
| T&C 26 | SaaS delivery: installation, domain integration and configuration; sole point of contact; no subcontracting; documentation; 30 working days | Configured, Operational | Terraform, Ansible, `h360-site-init`, `h360-setup`; portal on the institution's sub-domain (5.1); HyFib helpdesk as the single contact; this documentation |
| T&C 27 | On-site or virtual training for all identified users, at least 2 sessions within the implementation cost | Operational | [TRAINING-PLAN.md](TRAINING-PLAN.md): initial and refresher sessions per role, quick guides for students and parents |
| T&C 28 | Password-protected, role-based access; two-factor authentication for admin users; SSL | Implemented | 11 roles with per-DocType permissions and hostel/block scoping; 2FA (authenticator app) required for every desk role and for System Manager/Administrator; password policy and lockout; OTP for the portal; HTTPS only. `setup/roles.py`, `setup/install.py`, [SECURITY.md](SECURITY.md); tests `hostel_operations/tests/test_permissions.py`, `hostel_people/tests/test_scoping.py`, `auth/test_otp.py` |
| T&C 29 | National IT laws, education regulations and data privacy standards | Implemented, Operational | IT Act 2000 and rules; CERT-In directions (180-day logs in India, NIC/NPL time, 6-hour incident reporting with the Security Incident DocType); DPDP Act 2023 (notice before collection, consent capture, masked identifiers, export/erasure, breach handling). [SECURITY.md](SECURITY.md) §5–7; `security_incident` tests |

## D. Technical requirements

| Clause | Requirement | Status | How it is met | Where / evidence |
|---|---|---|---|---|
| TR 1 | Cloud server provided as per specification | Configured | Section B | `deploy/terraform/oci/` |
| TR 2 | 1-year contract extendable yearly up to 4 years; total cost for 4 years | Commercial | BoQ covers 4 years | — |
| TR 3 | Hosted on a MeitY-empanelled CSP, end to end including the AMC period | Configured | Oracle Cloud Infrastructure (Oracle India Private Limited), procured and managed by HyFib | bid document 13; `deploy/` |
| TR 4 | Cloud infrastructure sizing and bandwidth requirement | Configured | [DEPLOYMENT.md](DEPLOYMENT.md) §1; architecture document | `variables.tf`, `deploy/compose/compose.yml` |
| TR 5 | About 30 GB, 10 years of data migrated without loss; encrypted; retrievable | Implemented | Scripted, idempotent migration with dry runs and reconciliation of counts, money totals and file checksums; encrypted transfer and storage | `hostel_migration/engine.py`, `reconciliation.py`, Migration Run, Migration Record Map; tests `hostel_migration/tests/test_engine.py`, `test_mapping.py`, `test_transforms.py`, `test_sources.py`; reconciliation report and sign-off *(go-live)*; [DATA-MIGRATION-AND-EXIT.md](DATA-MIGRATION-AND-EXIT.md) |
| TR 6 | Limited customisation (forms, reports, dashboards, graphs) from the To-Be study at no additional cost | Operational | Delivered during implementation and tracked as Change Request tickets | Support Ticket category *Change Request*; monthly review |
| TR 7 | SMS, WhatsApp and e-mail gateway integration and upkeep at no cost; subscriptions and credits borne by ACGCET | Implemented | MSG91 or any HTTP SMS gateway with DLT, WhatsApp Cloud API, e-mail; English and Tamil templates; delivery log, retries, provider alerts | `hostel_communication/`; tests `hostel_communication/test_dispatch.py`, `hostel_communication/providers/test_providers.py`, `test_message_template.py`; DLT and WhatsApp template approvals *(go-live, items 5–7)* |
| TR 8 | Regular backups; ACGCET direct access to the backup repository | Configured, Operational | Section B 6.8; read-only bucket access for ACGCET officials and the escrowed restic password | `iam.tf` (`acgcet-backup-readers`); [BACKUP-AND-DR.md](BACKUP-AND-DR.md) §5 |
| TR 9 | At least three security layers including a firewall; redundancy | Configured, Implemented | Layer 1 OCI network and DDoS protection; layer 2 WAF, host firewall and malware scanning; layer 3 roles, 2FA, audit trail. Redundancy of data: encrypted off-site backups in a second region, versioned bucket, daily volume backups, automatic instance recovery. The application runs on one VM (no hot standby); recovery within the 4-hour RTO | [SECURITY.md](SECURITY.md) §1; [BACKUP-AND-DR.md](BACKUP-AND-DR.md) |
| TR 10 | 99.9 % monthly uptime; penalty schedule; 24×7×365 support | Commercial, Operational | Uptime measured and reported monthly; support model | Monthly Performance Report *(monthly)*; [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) §1 |
| TR 11 | Uptime 99.9 %; response within 1 hour; resolution within 4 hours; daily backup | Implemented, Operational | Support Ticket response and resolution SLAs with breach flags and escalation; daily backup with stale-backup alert | `hostel_operations/support.py`; tests `test_support_ticket.py`; `backup.stale` health check |
| TR 12 | Monthly server performance review in the first week, attended by an authorised technical representative | Operational | Review meeting procedure | [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) §8; minutes *(monthly)* |
| TR 13 | Monthly server performance report by the 6th | Implemented | Produced on the 1st at 07:00, e-mailed as PDF to the institution's officials; retried hourly and escalated from the 4th if not delivered | Monthly Performance Report; `hostel_operations/reports.py`; tests `test_monthly_performance_report.py`; sent reports *(monthly)* |
| TR 14 | Monthly security and performance audits; full cooperation | Operational | Institution Auditor access, logs and evidence on request | Institution Auditor role; runbook §8 |
| TR 15 | 1,500 concurrent active users without degradation; preventive and corrective maintenance | Implemented, **evidence pending** | k6 load test (1,500 portal + 40 desk users, p95 < 800 ms, < 1 % errors, ≥ 140 req/s) ready; smoke runs on the development bench recorded. The acceptance run on the staging stack must be run and its results committed before go-live | `tests/load/`; [LOAD-TESTING.md](LOAD-TESTING.md); `tests/load/results/` |
| TR 16 | Participation in review meetings | Operational | HyFib attends the monthly review and any meeting ACGCET calls | runbook §8 |
| TR 17 | Onsite support engineer at ACGCET when remote resolution fails, at no cost | Operational | Escalation of a ticket that cannot be resolved remotely within the SLA | runbook §6 |
| TR 18 | Dedicated helpdesk number, e-mail and online ticketing; 24×7×365 | Implemented, Operational | +91 96060 60596, support@hyfib.com, Support Ticket in the desk (*Get Support*), shown in the portal's Help page | Support Ticket; `frontend/src/views/HelpView.vue`; Hostel Settings `support_phone`, `support_email` |
| TR 19–21 | Data ownership with ACGCET; confidentiality; data stored only in India | Implemented, Configured | All data and backups in OCI Hyderabad and Mumbai; need-to-know access with 2FA and audit; confidential grievance and message rules; exit export and deletion on ACGCET's instruction | [SECURITY.md](SECURITY.md) §4, §7 |
| TR 22 | Exit management and data migration at no cost; deletion after written confirmation | Implemented | Full export in open formats with manifest and optional encryption, verification, deletion with a signed certificate, server decommission and cloud clean-up with a CERT-In log hold | `hostel_migration/export.py`, `verify.py`, `purge.py`, `certificate.py`, `deploy/EXIT.md`, `deploy/host/bin/h360-decommission`, `deploy/terraform/oci/exit-purge.sh`; tests `test_purge.py`, `test_verify.py`, `test_crypto.py`, `deploy/tests/exit/run.sh`, `deploy/tests/host/run.sh` |

## E. Payment terms

| Clause | Requirement | Status |
|---|---|---|
| II.1–2 | 25 % after implementation; 75 % in 12 equal quarterly instalments from year 2, subject to extension and SLA compliance | Commercial (accepted without deviation); SLA compliance evidenced by the Monthly Performance Reports |

## Open items

| Item | Clause | Plan |
|---|---|---|
| Acceptance load test on staging | TR 15 | Run `tests/load/run.sh load` on the production-shaped staging stack and commit the results before go-live. |
| Escalation level 2 contact, report recipients, DLT/WhatsApp approvals, campus egress addresses | 6.14, TR 7, TR 13 | Go-live checklist items 1–7. |
