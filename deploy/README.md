# HyFib Hostel360 — production deployment on Oracle Cloud Infrastructure

This directory holds everything needed to build, provision, run, back up and watch the
Hostel360 production environment for ACGCET Hostels:

| Path | What it is |
|---|---|
| `docker/` | Application image (`Containerfile`), its entrypoint and role scripts, `build.sh` |
| `compose/` | The production stack on one VM (`compose.yml`, `.env.example`) |
| `waf/` | Edge nginx + ModSecurity/OWASP CRS 4: vhost, headers, rate limits, Frappe rule exclusions |
| `mariadb/`, `redis/`, `clamav/`, `php-fpm/` | Service configuration mounted into the stack |
| `integration/website/` | PHP pages for the official hostel website (`/integration/`) |
| `terraform/oci/` | OCI infrastructure: network, Bastion, VM, volume, load balancer, bucket, IAM, Vault, alarms, logs, optional OCI WAF |
| `ansible/` | Server configuration: hardening, firewall, Docker, storage, malware scanning, stack, backups, monitoring, audit |
| `host/` | Host scripts (`bin/h360-*`), systemd units and timers, example configuration |
| `monitoring/` | External uptime monitor (Mumbai) and the OCI alarm runbook |
| `dev/` | Local development stack (not used in production) |

---

## 1. Architecture

```
 Students / parents / staff
          │ HTTPS (public certificate, TLS 1.2/1.3)
          ▼
 OCI Flexible Load Balancer 10–100 Mbps  (public subnet 10.20.1.0/24, NSG: 80/443)
          │ 80 → 301 HTTPS     │ re-encrypted to :8443 (or plain :8080)
          ▼
 VM.Standard.A1.Flex 8 OCPU / 16 GB, Ubuntu 24.04 arm64  (private subnet, no public IP)
 ufw + DOCKER-USER: 8443/8080 only from the LB subnet, SSH only from OCI Bastion
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ waf  nginx + ModSecurity + OWASP CRS 4 (PL1, anomaly 5), headers, rate limits │
 │   ├── frontend (nginx) ── backend (gunicorn 6×4) · websocket (socket.io)      │
 │   └── php-fpm (/integration/, pool of 300 workers, on demand)                 │
 │ queue-short ×2 · queue-long · scheduler · configurator (one-shot)             │
 │ mariadb 11.8 · redis-cache · redis-queue · clamav (clamd + freshclam)         │
 └──────────────────────────────────────────────────────────────────────────────┘
 /srv/hostel360 = 400 GB block volume (VPU 10): MariaDB, sites, Redis AOF, ClamAV, logs
          │ restic (AES-256) via rclone, instance principal
          ▼
 OCI Object Storage, Mumbai (versioned bucket)  +  OCI volume backups
```

Region: application in **ap-hyderabad-1**, backups in **ap-mumbai-1**; all data stays in India.

### Containers, networks and memory

| Service | Image | Networks | Memory limit |
|---|---|---|---|
| waf | `owasp/modsecurity-crs:4.30.0-nginx-alpine-202610051210` | edge, internal, php | 384 MB |
| frontend | Hostel360 image (`frontend` role) | internal | 192 MB |
| backend | Hostel360 image (`backend`, gunicorn gthread 6 workers × 4 threads) | internal, edge | 2 GB |
| websocket | Hostel360 image (`websocket`) | internal | 256 MB |
| queue-short ×2 | Hostel360 image (`worker short,default`) | internal, edge | 448 MB each |
| queue-long | Hostel360 image (`worker long,default,short`) | internal, edge | 640 MB |
| scheduler | Hostel360 image (`scheduler`) | internal | 256 MB |
| configurator | Hostel360 image (`configure`, one-shot) | internal | 512 MB |
| mariadb | `mariadb:11.8.9-noble` (buffer pool 6 GB) | internal | 7 GB |
| redis-cache | `redis:8.6.7-alpine3.23` (512 MB, LRU, no persistence) | internal | 576 MB |
| redis-queue | `redis:8.6.7-alpine3.23` (512 MB, AOF, noeviction) | internal | 640 MB |
| clamav | `clamav/clamav-debian:1.4.6` (LTS) | edge | 1.75 GB |
| php-fpm | `php:8.3.35-fpm-alpine3.23` | php | 768 MB |

- **edge**: the only network with a route out (SMS/WhatsApp/e-mail/Razorpay APIs, ClamAV
  mirrors) and the published WAF ports.
- **internal** (`internal: true`): application and data tier; MariaDB and Redis are
  reachable only here.
- **php** (`internal: true`): WAF and PHP-FPM only, so the PHP pages can never reach the
  database, Redis or the application.

Memory plan (bid): database 6–7 GB, application workers 4 GB, Redis 1 GB, the rest for
ClamAV, WAF, PHP-FPM and the OS. Limits are ceilings; normal use is well below. A 4 GB swap
file on the boot volume (vm.swappiness 10) absorbs short spikes. One clamd serves both the
application (scan-on-upload over TCP) and Linux Malware Detect on the host (over its unix
socket with descriptor passing), so the 1.2 GB signature database is loaded only once.

### Application image

`docker/Containerfile` is a two-stage, multi-architecture build (linux/arm64 primary,
linux/amd64):

1. **builder** (`frappe/build:v16.51.0`): `bench init` with Frappe pinned to the release
   tag **v16.52.0**, `hostel360` installed from this repository, `yarn install` (the root
   postinstall installs the portal SPA), `bench build --production --force` for every app
   (desk bundles and the Vite portal build), then git metadata and build-only
   `node_modules` are removed (only the three packages the realtime server loads are kept).
2. **runtime** (`frappe/base:v16.52.0`): the finished bench, non-root (`frappe`), OCI
   labels, a `HEALTHCHECK`, and the built assets in the image layer. At start-up the
   entrypoint links them into the sites volume, so a new image never serves stale assets.

One image runs every role; the first argument selects it (`backend`, `websocket`,
`worker <queues>`, `scheduler`, `frontend`, `configure`, `migrate`), anything else runs as a
command (`bench …`, `bash`).

---

## 2. Security controls

| Layer | Control | Where |
|---|---|---|
| Network | Server in a private subnet without public IP; NSG admits 8443/8080 only from the LB NSG; SSH only from the Bastion endpoint; egress limited to 80/443/465/587 and NTP | `terraform/oci/network.tf` |
| Network | OCI Bastion with an allow-list of HyFib office addresses and 3-hour sessions | `terraform/oci/bastion.tf` |
| Network | IMDSv2 only; containers cannot reach the metadata service (instance principal tokens) | `compute.tf`, `ansible/roles/firewall` |
| Edge | TLS 1.2/1.3 with forward-secret AEAD ciphers, HTTP→HTTPS redirect, header validation | `terraform/oci/lb.tf` |
| Edge | ModSecurity + OWASP CRS 4.30 (paranoia 1, inbound anomaly 5) with path- and argument-scoped Frappe exclusions | `waf/` |
| Edge | Rate limits on OTP sign-in (`hostel360.api.auth.*`), desk login and password reset; campus NAT exemptions | `waf/templates/conf.d/00-hostel360.conf.template` |
| Edge | HSTS, CSP (strict for the portal, Desk-compatible elsewhere; Razorpay allowed), X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP | `waf/templates/includes/h360-security-headers.conf.template` |
| Edge | Only the portal host name is served (421 otherwise); fail2ban bans abusive clients at the WAF (the real client address, not the load balancer) | `waf/`, `ansible/roles/fail2ban`, `host/bin/h360-waf-ban` |
| Host | ufw, SSH hardening (keys only, no root, modern algorithms), sysctl hardening, unattended security updates, auditd rules, login banner | `ansible/roles/{firewall,ssh,common,auditd}` |
| Host | ClamAV scan-on-upload (application) and Linux Malware Detect realtime monitor + daily scan with quarantine | `clamav/`, `ansible/roles/malware` |
| Containers | Non-root application image, `no-new-privileges`, all capabilities dropped (a minimal set added back only where an image's entrypoint needs it), read-only root filesystems for frontend, websocket, Redis cache and PHP-FPM | `compose/compose.yml` |
| Data | Block and object storage encrypted at rest (optional customer-managed key in OCI Vault), restic AES-256 backups in Mumbai, versioned bucket the server cannot purge, read-only bucket access for ACGCET officials | `terraform/oci/{storage,objectstorage,iam,vault}.tf` |
| Logs | 180 days: journald, rsyslog, WAF/ClamAV/MariaDB logs (logrotate), OCI Logging (LB access/error, VCN flow logs); time from NIC (samay1/samay2.nic.in) and NPL (time.nplindia.org) | `ansible/roles/common`, `terraform/oci/logging.tf` |

**Service worker scope.** The portal's service worker is served from
`/assets/hostel360/frontend/sw.js` and registered with scope `/hostel`, so the WAF sends
`Service-Worker-Allowed: /hostel` for that file (a value of `/hostel/` would not cover the
`/hostel` scope the portal registers).

**Website embed.** `/assets/hostel360/embed/` (the sign-in button script the institution
website loads, see `integration/website/README.md`) is answered with
`Cross-Origin-Resource-Policy: cross-origin` and `Access-Control-Allow-Origin: *`, so it can be
embedded and pinned with Subresource Integrity; nothing else on the portal is shared
cross-origin.

**WAF tuning.** Set `WAF_RULE_ENGINE=DetectionOnly` in `/etc/hostel360/stack.env` (or
`h360_waf_rule_engine` in Ansible) to log without blocking while a new exclusion is being
worked out; blocked requests are in `/srv/hostel360/logs/waf/error.log` with the rule id.

---

## 3. First installation

Prerequisites on the administrator's workstation: OCI CLI configured for the tenancy,
Terraform ≥ 1.9, Ansible core ≥ 2.17 with the collections in `ansible/requirements.yml`,
Docker with buildx, and an SSH key.

### 3.1 Build and push the image

```
export OCIR_REGION=ap-hyderabad-1 OCIR_NAMESPACE=<tenancy namespace>
docker login ap-hyderabad-1.ocir.io -u '<namespace>/<user>'      # auth token as password
deploy/docker/build.sh --push                                    # arm64 + amd64, SBOM, provenance
```

The tag is `<version>-<git sha>` (e.g. `1.0.0-94a56ce3e54a`); `latest` is never used and the
OCIR repository is immutable.

### 3.2 Provision OCI (Terraform)

```
cd deploy/terraform/oci
cp terraform.tfvars.example terraform.tfvars        # fill in; keep it out of git
terraform init
terraform plan -out plan.out
terraform apply plan.out
terraform output
```

Then point the portal's DNS A record at `lb_public_ip`. State: use the `oci` backend
(`backend.tf.example`) so state lives in a private, versioned bucket.

### 3.3 Configure the server (Ansible)

```
cd deploy/ansible
ansible-galaxy collection install -r requirements.yml -p collections
cp inventory.example.ini inventory.ini                       # Bastion session in ProxyCommand
cp group_vars/hostel360/main.yml.example group_vars/hostel360/main.yml
cp group_vars/hostel360/vault.yml.example group_vars/hostel360/vault.yml
ansible-vault encrypt group_vars/hostel360/vault.yml          # openssl rand -hex 32 for each secret
ansible-playbook site.yml --ask-vault-pass
```

Roles, in order: `common` (packages, Asia/Kolkata, chrony with NIC/NPL, unattended
upgrades, sysctl, journald and logrotate at 180 days, swap, administrators), `ssh`,
`storage` (format if blank, mount by label at `/srv/hostel360`, at least 4,000,000 inodes),
`firewall` (ufw, DOCKER-USER), `docker` (Docker CE + compose plugin, log rotation,
live-restore), `auditd`, `app` (stack files, host scripts, systemd units, secrets, WAF
certificate, `stack.env`, OCI CLI, image pull, stack start), `backup` (restic, rclone,
repository init, timers), `monitoring` (agent settings, timers), `fail2ban`, `malware`
(maldet 2.0.1 with realtime monitor and daily scan).

### 3.4 Create the site

```
sudo h360-site-init
```

Creates the site with `hostel360` installed, enables the scheduler, stores the
Administrator password in `/etc/hostel360/secrets/site_admin_password`, writes the ingest
secret and an OCI notification token into Ops Alert Settings, and prints the
`alarm_https_endpoint` to add to `terraform.tfvars` (then `terraform apply` again so OCI
alarms also arrive in Hostel360 as Ops Alerts). Then sign in as Administrator, enable
two-factor authentication and complete Hostel Settings, Payment Settings and Messaging
Settings.

### 3.5 Verify

```
sudo systemctl status hostel360-stack
sudo docker compose -p hostel360 --env-file /etc/hostel360/stack.env -f /opt/hostel360/deploy/compose/compose.yml ps
curl -s https://<site>/api/method/hostel360.api.health.readiness
sudo h360-agent && sudo journalctl -t h360-agent -n 20
sudo h360-backup && sudo h360-restore-test
```

---

## 4. Operations

### Deploy a release

```
deploy/docker/build.sh --push                 # workstation or CI
sudo h360-deploy 1.0.1-<sha>                  # on the server
sudo h360-deploy --status
sudo h360-deploy --rollback                   # back to the previous tag
```

`h360-deploy` logs in to OCIR with the instance principal, pulls the image, puts the site
in maintenance mode and stops the workers, dumps the database to
`/srv/hostel360/backups/pre-deploy/` (the rollback point), takes an offsite restic snapshot
and an OCI volume backup, runs the configurator and `bench migrate` from the **new** image,
switches the stack and checks readiness through the WAF. Any failure from the migration on
restores the dump, returns to the previous tag and raises a critical Ops Alert.

### Backups and restore

| What | When | Retention |
|---|---|---|
| restic: site databases (`mariadb-dump --single-transaction --routines`, binlog position recorded) | daily 01:30 IST | 30 daily, 12 weekly (Sunday), 12 monthly (1st) |
| restic: sites (uploads, site_config.json), `/etc/hostel360`, MariaDB binlogs, stack and host security logs | daily 01:30 IST | same |
| restic repository check (5% of the data) and prune | Sundays | — |
| Restore test into a throwaway MariaDB, compared with live | 2nd of each month, 03:00 IST | reported as "Restore Test" |
| OCI volume backups (data + boot) | daily incremental 02:30 IST, weekly and monthly full | 7 days, 4 weeks, 6 months |
| Pre-deploy / pre-restore snapshots | each deployment or restore | local dumps: last 5; volume backups: 14 days |

Every run is reported to Hostel360 as an Ops Backup Record; failures raise critical alerts.
The restic password must be escrowed with ACGCET (sealed envelope or their own vault):
without it the backups cannot be read. ACGCET officials get read-only bucket access through
the `acgcet-backup-readers` group.

```
sudo h360-restore list                        # snapshots
sudo h360-restore restore                     # latest database + files, guided
sudo h360-restore restore --db <id> --db-only
sudo h360-restore restore --host <old-host-label>     # disaster recovery on a new server
```

RPO 24 h (nightly backup; binlogs allow point-in-time replay), RTO 4 h (new server through
Terraform and Ansible ≈ 1 h, restore ≈ 1 h for the expected data size).

### Alerting

| Source | Signals | Path |
|---|---|---|
| Application health checks | database, Redis, queues, scheduler, backups age, certificate, disk, errors | Ops Alert → escalation matrix |
| `h360-agent` (every 5 min) | CPU, memory, swap, load, disk and inodes, container health and restarts, fail2ban bans, WAF blocks, ClamAV signature age, maldet monitor, internal certificate, NTP sync, pending reboot | HMAC-signed ingest → Ops Alert / metrics |
| `h360-uptime-probe` (every min) | public liveness URL through DNS, LB, WAF | `uptime_probe` samples → monthly availability; outage alert after 3 failures |
| `h360-maldet-hook` | malware found by maldet | critical security alert per scan |
| `h360-backup`, `h360-restore-test`, `h360-snapshot` | results | Ops Backup Records, alerts on failure |
| OCI Monitoring | CPU, memory, metrics absent, instance accessibility, LB unhealthy backends, LB 5xx | Notifications topic: e-mail, PagerDuty, Slack, Hostel360 |
| Uptime Kuma (Mumbai) | the portal from outside Hyderabad, application dead-man switch | its own channels |

When the application cannot accept a host alert, `h360-ingest` keeps it in
`/var/spool/hostel360/ingest` (retried for 24 h) and publishes critical alerts directly to
the OCI Notifications topic. Ingest messages are signed:
`X-H360-Signature = hex(HMAC-SHA256(ingest_secret, "<timestamp>.<body>"))`.

### Everyday commands

```
sudo journalctl -t h360-agent -t h360-backup -t h360-deploy --since today
sudo systemctl list-timers 'h360-*'
sudo h360-waf-ban list | ban <ip> | unban <ip>
sudo fail2ban-client status
sudo maldet --report list
sudo docker compose -p hostel360 --env-file /etc/hostel360/stack.env \
     -f /opt/hostel360/deploy/compose/compose.yml exec backend bench --site <site> console
```

### Host configuration files

| File | Contents | Example |
|---|---|---|
| `/etc/hostel360/stack.env` | compose settings, image tag | `compose/.env.example` |
| `/etc/hostel360/agent.env` | host agent, probe, ingest | `host/config/agent.env.example` |
| `/etc/hostel360/backup.env` | restic/rclone, volume backups | `host/config/backup.env.example` |
| `/etc/hostel360/secrets/` | database root password, restic password, site admin password, OCI notification token | written by Ansible / `h360-site-init` |
| `/etc/hostel360/waf/tls/` | internal certificate for LB re-encryption | Ansible |
| `/etc/hostel360/waf/lists/` | `denylist.conf` (fail2ban), `rate-exempt.conf` | Ansible |

---

## 5. Validation

The checks used for this directory, runnable on any machine with Docker:

```
docker compose -f deploy/compose/compose.yml --env-file deploy/compose/.env.example config --quiet
docker run --rm -v "$PWD":/mnt -w /mnt koalaman/shellcheck:v0.11.0 -x \
    deploy/host/bin/h360-* deploy/docker/entrypoint.sh deploy/docker/build.sh deploy/docker/bin/*
cd deploy/terraform/oci && docker run --rm -v "$PWD":/w -w /w hashicorp/terraform:1.16.6 fmt -check -recursive
cd deploy/terraform/oci && docker run --rm -v "$PWD":/w -w /w -e TF_DATA_DIR=/tmp/tf hashicorp/terraform:1.16.6 \
    sh -c 'terraform init -backend=false -input=false >/dev/null && terraform validate'
cd deploy/ansible && ansible-playbook --syntax-check site.yml && ansible-lint site.yml roles
deploy/docker/build.sh                        # local image for this machine's platform
```

## 6. Tender clauses covered here

| Clause | Requirement | How |
|---|---|---|
| 5.1 | Integrated with the official hostel website | `/integration/` pages and `links.php` menu feed (CORS for the website's origin) |
| 6.1–6.3 | 6 CPU, 12 GB RAM, 300 GB NVMe | 8 OCPU Ampere A1, 16 GB, 400 GB balanced block volume |
| 6.4 | 4,000,000 inodes | ext4 with one inode per 16 KB (~26 million), asserted by Ansible |
| 6.5 | 300 PHP workers | PHP-FPM pool `pm = ondemand`, `pm.max_children = 300` |
| 6.6 | Location: India | Hyderabad (primary), Mumbai (backups) |
| Security | Firewall, WAF, malware scanning, HTTPS, encrypted backups, CERT-In log retention and NTP | section 2 |
