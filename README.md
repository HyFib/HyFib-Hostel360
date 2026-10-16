# HyFib Hostel360

Cloud-based hostel management software by HyFib Solutions Private Limited, Chennai.

Hostel360 runs a college hostel end to end on one secure platform: registrations, rooms and
beds, fees and online payment, the mess (stock, menu, reductions and monthly billing),
home-visit leave, grievances, bonafide certificates and feedback. Wardens and office staff
work on a role-based web desk with two-factor sign-in; students and parents use a
mobile-first portal in English and Tamil, signing in with a one-time code sent to their
registered mobile number. HyFib's operations team watches the installation around the clock
through built-in health checks, alerting with an escalation matrix, backups and monthly
performance reports.

This repository is the Frappe application (`hostel360`), the student and parent portal
(`frontend/`), the production deployment on Oracle Cloud Infrastructure (`deploy/`) and the
documentation (`docs/`).

**Deployment: ACGCET Hostels, Karaikudi** — Alagappa Chettiar Government College of
Engineering and Technology, tender ACGCET/4903/B1/2026. Hosted on OCI Hyderabad
(ap-hyderabad-1) with backups in OCI Mumbai (ap-mumbai-1); all data stays in India.

---

## Features by module

| Module | What it does |
|---|---|
| **Hostel Setup** | Hostels, blocks, floors, rooms and beds with capacity, facilities and live occupancy counters; academic years, departments and programmes; institution settings and the privacy notice. |
| **People and Rooms** | Student register with photo, guardians and validation against the college register; bulk import from Excel/CSV with a row-by-row check; staff registration with desk logins, roles and hostel/block assignments; room allotment, transfer and vacating with full history; the visual **Room Board**. |
| **Fees** | Fee heads and structures per hostel, year and programme; one-click publishing of dues; invoices, counter receipts and online payment through Razorpay or PayU straight to the institution's merchant account; automatic receipts, reminders and gateway reconciliation; collection, dues and reconciliation reports. |
| **Mess** | Stock receipts and issues with moving-average valuation; indents with approval; weekly menu planner published to students and parents; mess reduction applications with configurable rules; extras and expenses; monthly bills on the dividing system turned into fee invoices; consumption, stock and cost reports. |
| **Student Services** | Home-visit leave with parent and warden approval, gate check-out and return, overdue tracking and an automatic mess reduction; grievances with SLA, escalation, confidential handling of ragging and harassment complaints, ratings and reopening; bonafide certificates with QR verification; monthly feedback; one **Approvals** inbox and a **Gate Pass** screen for security staff. |
| **Student and parent portal** | Installable web app at `/hostel`: dues and online payment, receipts, fee structure, mess menu and bills, mess reduction, leave (with parent approvals), grievances with photos, feedback, certificates and notifications, in English and தமிழ். |
| **Communication** | SMS (DLT, MSG91 or any HTTP gateway), WhatsApp Cloud API, e-mail and in-app notifications from editable templates in English and Tamil; delivery log with retries. |
| **HyFib Operations** | Five-minute health checks, host agent metrics, uptime probes, OCI alarms and security signals as alerts with de-duplication and an L1/L2/L3 escalation matrix; support tickets with 1-hour response and 4-hour resolution SLAs; backup and restore-test records; security incidents with the six-hour CERT-In clock; monthly performance report e-mailed to the institution by the 6th. |
| **Data Migration and Exit** | Scripted, repeatable migration of legacy data with reconciliation of counts, money and file checksums; full exit export (CSV, Excel, SQL, files, manifest; optional encryption); verified deletion with a signed certificate. |

Security is built in at three layers — OCI network controls, a ModSecurity/OWASP CRS web
application firewall with malware scanning, and the application's own role-based access,
two-factor authentication, OTP sign-in and audit trail. See [docs/SECURITY.md](docs/SECURITY.md).

## Architecture

```
Students, parents, staff ──HTTPS──▶ OCI Load Balancer ──▶ VM (private subnet, Docker Compose)
                                                          ├─ WAF: nginx + ModSecurity + OWASP CRS 4
                                                          ├─ Frappe: gunicorn, websocket, workers, scheduler
                                                          ├─ MariaDB 11.8, Redis (cache, queue)
                                                          ├─ ClamAV, PHP-FPM (website integration)
                                                          └─ restic ──▶ OCI Object Storage (Mumbai)
```

One VM.Standard.A1.Flex (8 OCPU, 16 GB, 400 GB block volume) behind an OCI Load Balancer,
reached by administrators only through OCI Bastion. Diagrams of the components, the request
path through the three security layers, payments and alerting are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); the authoritative data model and interfaces are
in [docs/SOLUTION-DESIGN.md](docs/SOLUTION-DESIGN.md).

| Layer | Technology |
|---|---|
| Application | Frappe Framework v16 (Python 3.14), MariaDB 11.8, Redis 8 |
| Portal | Vue 3, TypeScript, Vite, Tailwind CSS, installable PWA |
| Edge | OCI Load Balancer, nginx + ModSecurity + OWASP CRS 4, fail2ban |
| Infrastructure | Terraform (OCI), Ansible, Docker Compose, systemd timers |

## Quick start (development)

Requirements: Docker with Compose, about 8 GB of free memory.

```bash
docker compose -f deploy/dev/compose.yml up -d
docker exec -it h360-dev-bench-1 bash
```

Inside the container, the first time only:

```bash
cd /home/frappe
bench init --ignore-exist --skip-redis-config-generation --frappe-branch version-16 --dev frappe-bench
cd frappe-bench
bench set-config -g db_host mariadb
bench set-config -g redis_cache redis://redis-cache:6379
bench set-config -g redis_queue redis://redis-queue:6379
bench set-config -g redis_socketio redis://redis-queue:6379
bench get-app /workspace/hostel360 --soft-link
bench new-site h360.localhost --db-root-password devroot --admin-password admin --install-app hostel360
bench --site h360.localhost set-config -p developer_mode 1
bench use h360.localhost
bench start
```

Then open http://localhost:8000/desk (Administrator / admin) and http://localhost:8000/hostel
for the portal. `bench --site h360.localhost h360-demo-data` loads a realistic demonstration
data set. Day-to-day work, tests, the portal dev server and conventions are in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Documentation

| Document | For |
|---|---|
| [Solution design](docs/SOLUTION-DESIGN.md) | Data model, roles, permissions, services, API and hooks: the contract |
| [Architecture](docs/ARCHITECTURE.md) | Components, security layers, payment and alerting flows |
| [API reference](docs/API.md) | Portal, sign-in, payment and operations endpoints |
| [Deployment](docs/DEPLOYMENT.md) | OCI sizing against the tender; points to `deploy/README.md` |
| [Operations runbook](docs/OPERATIONS-RUNBOOK.md) | 24×7 support model, escalation, alert catalogue, routine and monthly tasks |
| [Backup and disaster recovery](docs/BACKUP-AND-DR.md) | RPO/RTO, schedules, restore and monthly restore test |
| [Security](docs/SECURITY.md) | Controls by layer, 2FA, OTP, uploads, WAF, CERT-In, DPDP |
| [Data migration and exit](docs/DATA-MIGRATION-AND-EXIT.md) | TR 5 migration and TR 22 exit procedures |
| [Compliance matrix](docs/COMPLIANCE-MATRIX.md) | Every tender clause mapped to features, code and evidence |
| [Load testing](docs/LOAD-TESTING.md) | 1,500-user capacity test |
| [Training plan](docs/TRAINING-PLAN.md) | Sessions per role and materials |
| [User manuals](docs/user-manual/README.md) | Step-by-step guides for every role, and a Tamil quick guide for students and parents |
| [Development](docs/DEVELOPMENT.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) | Engineering |

## Repository layout

```
hostel360/        Frappe app: modules, portal API, security, bench commands
frontend/         Student and parent portal (Vue 3)
deploy/           Image build, Compose stack, WAF, Terraform, Ansible, host scripts, monitoring, exit runbook
docs/             Design, operations and user documentation
tests/load/       k6 load test
.github/          CI, security scanning, image build and deployment workflows
```

## Support and security

- Helpdesk (24×7): **+91 96060 60596**, support@hyfib.com
- Report a vulnerability: security@hyfib.com — see [SECURITY.md](SECURITY.md)

© 2026 HyFib Solutions Private Limited. See [LICENSE](LICENSE).
