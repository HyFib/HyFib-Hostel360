# Deployment

The complete, step-by-step production procedure — image build, Terraform, Ansible, site
creation, two-factor enrolment, verification, the go-live checklist, deployments, backups,
alerting and the exit — is in **[`deploy/README.md`](../deploy/README.md)**. This page
summarises the sizing against the tender and how releases reach the server.

## 1. OCI sizing against the tender

| Clause | Tender requirement | Provided | Where it is set |
|---|---|---|---|
| 6.1 | 6 CPU cores | 8 OCPU (Ampere A1, arm64) | `deploy/terraform/oci/variables.tf` `instance_ocpus` |
| 6.2 | 12 GB RAM | 16 GB | `instance_memory_gbs` |
| 6.3 | 300 GB NVMe | 400 GB NVMe-backed block volume, Balanced (VPU 10), expandable | `data_volume_size_gbs`, `data_volume_vpus_per_gb` |
| 6.4 | 4,000,000 inodes | ext4 with one inode per 16 KB (about 26 million), asserted by Ansible | `deploy/ansible/roles/storage` |
| 6.5 | 300 PHP workers | PHP-FPM `pm = ondemand`, `pm.max_children = 300` | `deploy/php-fpm/` |
| 6.6 | Location India | Hyderabad (primary), Mumbai (backups) | `region`, `backup_region` |
| 6.7 | 100 × 10,000 transactions | No per-transaction limit; sized for at least 10 lakh transactions | — |
| 6.8 | Daily/weekly/monthly backups | restic daily (30), weekly (12), monthly (12) to Object Storage Mumbai, plus OCI volume backups | [BACKUP-AND-DR.md](BACKUP-AND-DR.md) |
| 6.9, 6.10 | SSL, site security, malware protection | TLS 1.2/1.3 at the LB, ModSecurity + OWASP CRS 4, ClamAV + Linux Malware Detect | [SECURITY.md](SECURITY.md) |
| 6.11 | High-speed bandwidth, 99.9 % uptime | OCI Flexible LB 10–100 Mbps (raised at no cost if needed), monitored every minute from the VM and from Mumbai | `lb_min_bandwidth_mbps`, `lb_max_bandwidth_mbps` |
| 6.12 | Access sharing | Institution Auditor desk role (dashboards, reports, Operations Status) and read-only backup bucket access (`acgcet-backup-readers`) | `deploy/terraform/oci/iam.tf` |
| TR 15 | 1,500 concurrent users | gunicorn 6 workers × 4 threads, 2 short + 1 long worker, MariaDB buffer pool 6 GB; verified with the k6 load test on staging | [LOAD-TESTING.md](LOAD-TESTING.md) |

Memory plan: database 6–7 GB, application workers about 4 GB, Redis 1 GB, the rest for
ClamAV, the WAF, PHP-FPM and the operating system, with a 4 GB swap file for short spikes.
Container limits are listed in `deploy/README.md` §1.

## 2. Environments

| Environment | Purpose | Notes |
|---|---|---|
| Production | ACGCET Hostels | `h360_environment: production`; Ops Alert Settings environment Production |
| Staging | UAT and the acceptance load test | Same shape as production; `h360_environment: staging` relaxes only the campus-address checks |
| Development | Engineering | `deploy/dev/compose.yml`, see [DEVELOPMENT.md](DEVELOPMENT.md) |

## 3. Continuous integration and delivery

| Workflow | Trigger | What it does |
|---|---|---|
| `.github/workflows/ci.yml` | push to `main`, pull requests | ruff lint and format check; portal type check, unit tests and production build; server tests on MariaDB 11.8 + Redis with Frappe v16.52.0, Python 3.14 and Node 24, with coverage |
| `.github/workflows/security.yml` | push to `main`, pull requests, weekly | CodeQL (Python, JavaScript/TypeScript), gitleaks, osv-scanner, pip-audit of the Frappe dependency set, yarn audit, Trivy filesystem and configuration scans of `deploy/`, Checkov on Terraform, ShellCheck |
| `.github/workflows/image.yml` | tags `v*`, manual | `deploy/docker/build.sh --push`: linux/arm64 + linux/amd64 to OCIR with SBOM and provenance |
| `.github/workflows/deploy.yml` | manual only, protected environment | Opens an OCI Bastion port-forwarding session and runs `sudo h360-deploy <tag>` on the server, then checks readiness |

Releases: tag the commit `vX.Y.Z` (matching `__version__` in `hostel360/__init__.py`); the
image workflow pushes `X.Y.Z-<sha>`; an authorised person runs **Deploy** with that tag and
the `production` environment's reviewers approve it. `h360-deploy` takes a database dump,
an off-site snapshot and a volume backup first, runs the migration from the new image and
rolls back automatically if anything fails.

### Secrets and variables

Set in GitHub under Settings > Environments (`production`, and `staging` if used). Nothing
here is ever printed by the workflows.

| Name | Kind | Used by | Value |
|---|---|---|---|
| `OCIR_REGION` | variable | image | `ap-hyderabad-1` |
| `OCIR_NAMESPACE` | variable | image | tenancy Object Storage namespace |
| `OCIR_USERNAME` | secret | image | `<namespace>/<user>` (or `<namespace>/oracleidentitycloudservice/<user>`) of a CI user that may push to the `hostel360` repository only |
| `OCIR_AUTH_TOKEN` | secret | image | that user's auth token |
| `OCI_CLI_USER` | secret | deploy | OCID of the deploy user (API key authentication) |
| `OCI_CLI_TENANCY` | secret | deploy | tenancy OCID |
| `OCI_CLI_FINGERPRINT` | secret | deploy | API key fingerprint |
| `OCI_CLI_KEY_CONTENT` | secret | deploy | API private key (PEM) |
| `OCI_CLI_REGION` | variable | deploy | `ap-hyderabad-1` |
| `H360_BASTION_ID` | variable | deploy | `terraform output bastion_id` |
| `H360_INSTANCE_IP` | variable | deploy | `terraform output instance_private_ip` |
| `H360_PUBLIC_URL` | variable | deploy | `https://<portal host name>` |
| `H360_DEPLOY_SSH_KEY` | secret | deploy | private key of a deploy account on the server: an entry in `h360_admin_users` (Ansible), which puts it in `hostel360-admins` with sudo |
| `H360_DEPLOY_SSH_USER` | variable | deploy | that account's login (default `ubuntu`) |
| `H360_SSH_KNOWN_HOSTS` | secret | deploy | the server's host key, pinned, as `hostel360-app <type> <key>` (the contents of `/etc/ssh/ssh_host_ed25519_key.pub` on the server, prefixed with `hostel360-app`) |
| `DEPLOY_RUNS_ON` | variable | deploy | JSON runner label, e.g. `["self-hosted","hyfib-office"]` |

The OCI Bastion only accepts sessions from the addresses in `bastion_client_cidrs`. GitHub's
hosted runners have no fixed address, so the deploy job runs on a self-hosted runner at the
HyFib office (or on the HyFib VPN) whose egress address is on that list; set
`DEPLOY_RUNS_ON` accordingly. The deploy user's OCI group needs only `use bastion` and
`manage bastion-session` in the compartment. The workflow makes a new key for each Bastion
session, deletes the session at the end, and never prints a secret.

CodeQL needs GitHub code scanning (GitHub Code Security on a private repository). Where it is
not available, set the repository variable `ENABLE_CODEQL` to `false`; the other security jobs
do not depend on it.

## 4. Go-live

Nothing opens to students until every line of the go-live checklist in `deploy/README.md`
§3.7 is ticked: campus egress addresses, report recipients, the escalation level 2 contact,
security incident contacts, DLT and WhatsApp template approvals, test mode off, gateway keys
and webhooks, one real end-to-end payment, outbound connectivity, the heartbeat URL, the
restic password escrowed with ACGCET, outgoing e-mail and every desk user enrolled in
two-factor authentication.

## 5. End of contract

The exit (TR 22) is in `deploy/EXIT.md` and [DATA-MIGRATION-AND-EXIT.md](DATA-MIGRATION-AND-EXIT.md).
