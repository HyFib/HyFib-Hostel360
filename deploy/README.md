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
| `monitoring/` | External uptime monitor (Mumbai) with its ingest relay, the OCI alarm runbook and the security incident runbook |
| `EXIT.md` | End of contract (TR 22): export, deletion, server decommission, cloud clean-up, CERT-In log hold |
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
 │ queue-short ×2 · queue-long (alerts queue first) · scheduler · configurator   │
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
| queue-short ×2 | Hostel360 image (`worker alerts,short,default`) | internal, edge | 448 MB each |
| queue-long | Hostel360 image (`worker alerts,long,default,short`) | internal, edge | 640 MB |
| scheduler | Hostel360 image (`scheduler`) | internal | 256 MB |
| configurator | Hostel360 image (`configure`, one-shot) | internal | 512 MB |
| mariadb | `mariadb:11.8.9-noble` (buffer pool 6 GB) | internal | 7 GB |
| redis-cache | `redis:8.6.7-alpine3.23` (512 MB, LRU, no persistence) | internal | 576 MB |
| redis-queue | `redis:8.6.7-alpine3.23` (512 MB, AOF, noeviction) | internal | 640 MB |
| clamav | `clamav/clamav-debian:1.4.6` (LTS) | edge | 1.75 GB |
| php-fpm | `php:8.3.35-fpm-alpine3.23` | php | 768 MB |

- **edge**: the only network with a route out (SMS/WhatsApp/e-mail/Razorpay/PayU APIs,
  ClamAV mirrors) and the published WAF ports. Outbound connections are listed under
  *Outbound connections* in section 2.
- **internal** (`internal: true`): application and data tier; MariaDB and Redis are
  reachable only here. The WAF is also on it under the portal host name (`SITE_NAME`, a
  network alias) and listens there on 443 and 80 as well - see *Print PDFs* below.
- **php** (`internal: true`): WAF and PHP-FPM only, so the PHP pages can never reach the
  database, Redis or the application.

**Job queues.** Besides Frappe's `short`, `default` and `long`, the stack has an `alerts`
queue for HyFib operations alert deliveries only, so a page never waits behind thousands of
fee or mess-bill notifications. The configurator defines it before any worker starts
(`workers.alerts` with a 300 s timeout in `common_site_config.json`, and
`hostel360_alert_queue: alerts` in the site's config), and every worker lists it first. A
worker started with a queue the configuration does not define leaves it out with a warning
instead of failing; the application then sends alerts straight from the request that raised
them. When upgrading a server to this layout, deploy the new image with `h360-deploy` (which
runs the new configurator first) before applying a playbook that brings the new compose file.

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
| Edge | Rate limits on OTP sign-in (`hostel360.api.auth.*`), desk login and password reset; the campus egress addresses are exempt (see 3.3) | `waf/templates/conf.d/00-hostel360.conf.template` |
| Edge | HSTS, CSP (strict for the portal, Desk-compatible elsewhere; Razorpay Checkout and the PayU checkout form post allowed), X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP | `waf/templates/includes/h360-security-headers.conf.template` |
| Edge | Only the portal host name is served (421 otherwise); fail2ban bans clients with repeated failed sign-ins (401/403, never throttled 429s), CRS blocks or rate-limit excess at the WAF (the real client address, not the load balancer); the campus egress addresses and the stack's internal network are never banned | `waf/`, `ansible/roles/fail2ban`, `host/bin/h360-waf-ban` |
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

**Payment gateways.** Razorpay Checkout runs in an overlay on the fees page (its script,
frames and API are in the portal's CSP). PayU is a full-page form post from the fees page to
PayU's hosted checkout, so the portal's `form-action` also allows `https://secure.payu.in`
and `https://test.payu.in` (test mode). PayU returns the payer with a cross-site form post to
`/api/method/hostel360.api.payments.payu_return` and notifies
`/api/method/hostel360.api.payments.payu_webhook`; the values of those posts are bank and
gateway text (including HTML fragments from some issuers), so ModSecurity does not inspect
argument values on exactly these two paths (rule 10111). Headers, cookies, argument names and
the protocol rules are still enforced, and the application trusts nothing posted until the
PayU hash checks out and PayU's verify API confirms the payment.

**Print PDFs.** Frappe renders receipts, certificates, invoices and the monthly report with
wkhtmltopdf, which loads the logo and other `/files` and `/assets` URLs from the site's public
URL (`host_name`, set by the configurator to `PUBLIC_URL`). Inside the stack the portal host
name resolves to the WAF (network alias on `internal`), which also listens on 443 (internal
certificate, accepted by wkhtmltopdf) and 80, so the backend and the workers fetch them
locally on the same scheme and port as the public URL. Without this the PDF engine would have
to leave the host through the NAT gateway and come back through the load balancer, and where
the public name does not resolve from the server (staging, before the DNS change) every PDF
failed with `HostNotFoundError`. These two ports are not published; the host firewall and
the load balancer are unchanged.

**Outbound connections.** The server's network security group allows outbound TCP 80, 443,
465 and 587 and NTP to any address (`app_egress_tcp_ports`); ufw allows outgoing traffic; and
the backend and every worker container sit on the `edge` network, so they reach the services
below without per-host rules. The scheduler, frontend, websocket, MariaDB and Redis cannot
leave the host at all.

| Destination | From | Why |
|---|---|---|
| `info.payu.in` (live), `test.payu.in` (test mode), HTTPS | backend, workers | PayU Verify Payment API: every PayU return and webhook, and the 15-minute reconciliation (workers) |
| `api.razorpay.com`, HTTPS | backend, workers | orders, payment checks, refunds, reconciliation |
| SMS gateway (MSG91: `control.msg91.com`) and `graph.facebook.com`, HTTPS | workers, backend | SMS (DLT) and WhatsApp messages, sign-in codes |
| the outgoing mail server, 465/587 | workers | e-mail notifications, monthly report |
| `database.clamav.net` (freshclam), HTTPS | clamav | malware signatures |
| OCI services (Object Storage, Registry, Monitoring, Notifications) | host | backups, image pulls, metrics, alert fallback; through the service gateway |

Check from the containers after go-live (any HTTP status shows the connection works):
`docker exec hostel360-queue-long-1 curl -s -o /dev/null -w '%{http_code}\n' https://info.payu.in/merchant/postservice.php?form=2`
(and `https://test.payu.in/...` while in test mode). If the NSG egress list is ever narrowed,
PayU's and Razorpay's API hosts must stay reachable from the backend and the workers.

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

**Campus egress addresses (required for go-live).** Students and staff on the hostel and
college Wi-Fi all reach the portal from the campus gateway's few public addresses. Treated as
one client, the campus would be throttled after 20 sign-in codes an hour, and the refusals
would get it banned at the WAF. Before the first run, get the public egress address(es) from
ACGCET's network administrator (or run `curl -s https://ifconfig.me` on a campus PC) and set
them in `group_vars/hostel360/main.yml`:

```
h360_environment: production
h360_campus_egress_cidrs:
  - <public address or range of the campus gateway>
```

From this one list the playbook exempts the campus from the WAF's sign-in rate limits
(`/etc/hostel360/waf/lists/rate-exempt.conf`), from fail2ban (`ignoreip`), and from the OTP
limit of 20 codes an hour per address (Messaging Settings > Trusted Networks, written by
`h360-site-init`, which Ansible also runs once the site exists). The per-number limits
(5 codes an hour, 60 s apart, 5 wrong codes) still apply to everyone. In production the
playbook stops before changing anything if the list is empty, or contains a private,
documentation (the example's placeholder) or over-wide range; `h360_environment: staging`
relaxes the first two checks for test servers. When the campus address changes, update the
list and run `ansible-playbook site.yml --tags app,fail2ban`; an address dropped from the
list is deleted by hand in Messaging Settings (the playbook only adds).

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
secret and an OCI notification token into Ops Alert Settings, adds the campus egress
addresses to Messaging Settings > Trusted Networks, and prints the `alarm_https_endpoint` to
add to `terraform.tfvars` (then `terraform apply` again so OCI alarms also arrive in
Hostel360 as Ops Alerts). It also defines the `alerts` job queue for the site (through the
configurator, see *Job queues*). The settings people have to supply are collected in the
go-live checklist (3.7).

### 3.5 First sign-in and two-factor enrolment

Two-factor authentication with an authenticator app ("OTP App": Google Authenticator,
Microsoft Authenticator, FreeOTP, ...) is switched on for the site and required for the
Administrator, every System Manager and every Hostel360 desk role. Frappe normally e-mails a
QR code at a user's first desk sign-in; on a new site no outgoing e-mail account exists yet
(the first sign-in would fail with "Please setup default outgoing Email Account"), and setting
one up needs a desk sign-in. So the Administrator is enrolled from the server:

1. On the server, with the person who will hold the Administrator account present:

   ```
   sudo h360-2fa-enrol Administrator
   ```

   It shows a QR code (and the key in groups of four) for the authenticator app, then asks
   for the code the app shows; the enrolment counts only when that code is right, so a
   mistyped key cannot lock anyone out. The key appears on this terminal only - do not
   redirect the output to a file.
2. Sign in at `https://<site>/login` as Administrator with the password from
   `/etc/hostel360/secrets/site_admin_password` and the 6-digit code from the app.
3. Set up outgoing e-mail (Email Account, "Default Outgoing") and give the Administrator
   user a real mailbox, so other users receive their QR code by e-mail from now on.
4. Create a named user with the System Manager role for each HyFib administrator (ACGCET
   officials get Institution Auditor, wardens and office staff their Hostel360 role); the
   shared Administrator account is for emergencies only (keep its password and the enrolled
   phone in the operations vault). Every desk user is asked at the first sign-in to scan the
   QR code e-mailed to them; the link is valid for 4 minutes (System Settings, "Expiry time
   of QR Code Image Page"), so they should keep the sign-in page open. Anyone who cannot
   receive it is enrolled in person with `sudo h360-2fa-enrol <user e-mail>`.
5. Complete Hostel Settings, Payment Settings (gateway Razorpay or PayU) and Messaging
   Settings; check that Trusted Networks lists the campus egress addresses.

A lost or replaced phone: `sudo h360-2fa-enrol --reset <user>` issues a new key (the old one
stops working at once) and enrols the new phone; a System Manager can also use "Reset OTP
Secret" on the user in the desk.

### 3.6 Verify

```
sudo systemctl status hostel360-stack
sudo docker compose -p hostel360 --env-file /etc/hostel360/stack.env -f /opt/hostel360/deploy/compose/compose.yml ps
curl -s https://<site>/api/method/hostel360.api.health.readiness
sudo h360-agent && sudo journalctl -t h360-agent -n 20
sudo h360-backup && sudo h360-restore-test
```

### 3.7 Go-live checklist

Nothing below has a safe default: each item is a fact only ACGCET or HyFib can supply, and
the portal must not open to students until every line is ticked. The health checks raise a
Warning alert for the two Ops Alert Settings items while they are empty.

| # | Item | Where | Who supplies it |
|---|---|---|---|
| 1 | Campus egress addresses (public address or range of the hostel and college gateway) | `h360_campus_egress_cidrs` (3.3); check Messaging Settings > Trusted Networks | ACGCET network administrator |
| 2 | **Report Recipients**: e-mail addresses of the institution officials who receive the Monthly Performance Report | Ops Alert Settings > Report Recipients | ACGCET |
| 3 | **Escalation level 2** (HyFib technical lead): e-mail and mobile | Ops Alert Settings > Escalation Levels, level 2 | HyFib |
| 4 | Security incident contacts: the deputy incident owner (normally the level 2 lead) and the ACGCET official to inform within six hours | [monitoring/security-incident-runbook.md](monitoring/security-incident-runbook.md), contacts table | HyFib, ACGCET |
| 5 | SMS on the DLT portal: Principal Entity ID, the approved header (sender ID), and a DLT content template ID (plus the MSG91 flow ID with MSG91) for every enabled SMS template, including the operations alert template | Messaging Settings; Message Template > DLT Template ID / MSG91 Flow Template ID | ACGCET (the registered entity), HyFib |
| 6 | WhatsApp: Business account, phone number ID, a permanent system-user token, and every WhatsApp template approved in WhatsApp Manager under the name and language entered in Hostel360 (alert template too) | Messaging Settings; Message Template > WhatsApp Template Name | ACGCET's Meta business account, HyFib |
| 7 | Messaging Settings > Test Mode switched **off** once 5 and 6 are approved, after a test send to a HyFib number on each channel | Messaging Settings | HyFib |
| 8 | Payment gateway keys of ACGCET's merchant account: Razorpay key ID, key secret and webhook secret, or PayU merchant key and salt; **Test Mode off** for live collection | Hostel Payment Settings | ACGCET (merchant), HyFib enters them |
| 9 | Razorpay webhook: URL `https://<site>/api/method/hostel360.api.payments.razorpay_webhook`, events `payment.captured`, `payment.failed`, `order.paid` and `refund.processed`, same secret as in Payment Settings | Razorpay Dashboard > Webhooks | ACGCET's Razorpay account |
| 10 | PayU: payment webhook (successful and failed payments) to `https://<site>/api/method/hostel360.api.payments.payu_webhook`; the return URL `https://<site>/api/method/hostel360.api.payments.payu_return` is sent with every payment and needs no dashboard setting. The settings form shows both | PayU Dashboard > Webhooks | ACGCET's PayU account |
| 11 | One real payment of a small amount end to end (receipt issued, webhook received, reconciliation clean) and refunded | portal, Hostel Payment Transaction | HyFib with ACGCET |
| 12 | Outbound HTTPS from the backend and workers to the gateway's API (*Outbound connections*, section 2) | server | HyFib |
| 13 | Heartbeat URL (Uptime Kuma push) in Ops Alert Settings; the Kuma ingest relay sending `uptime_external` samples | [monitoring/README.md](monitoring/README.md) | HyFib |
| 14 | Restic password escrowed with ACGCET (sealed envelope or their vault); ACGCET officials in the `acgcet-backup-readers` group | *Backups and restore* | HyFib, ACGCET |
| 15 | Outgoing e-mail account working (3.5) and every desk user enrolled in two-factor authentication | desk | HyFib |

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
| Pre-deploy / pre-restore snapshots | each deployment or restore | local dumps: last 5; volume backups: 14 days (`H360_SNAPSHOT_KEEP_DAYS`), set on each backup as its OCI retention period, so it expires even if the server and its prune timer are gone |

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
| `h360-uptime-probe` (every min) | public liveness URL through DNS, LB, WAF | `uptime_probe` samples → monthly availability; one outage alert after 3 failed minutes, repeated hourly while it lasts |
| `h360-maldet-hook` | malware found by maldet | critical security alert per scan |
| `h360-backup`, `h360-restore-test`, `h360-snapshot` | results | Ops Backup Records, alerts on failure |
| OCI Monitoring | CPU, memory, metrics absent, instance accessibility, LB unhealthy backends, LB 5xx | Notifications topic: e-mail, PagerDuty, Slack, Hostel360 |
| Uptime Kuma (Mumbai) | the portal from outside Hyderabad, application dead-man switch | its own channels; readiness results relayed to Hostel360 as `uptime_external` samples, which the availability figure prefers (they see VM and region outages the on-host probe cannot) |

When the application cannot accept a host alert, `h360-ingest` keeps it in
`/var/spool/hostel360/ingest` (retried for 24 h, at most 2,000 messages) and publishes
critical alerts directly to the OCI Notifications topic. Uptime probe samples are kept for
35 days instead, so a long outage still reaches the monthly report as measured downtime.
Ingest messages are signed:
`X-H360-Signature = hex(HMAC-SHA256(ingest_secret, "<timestamp>.<body>"))`, and each carries
a `message_id` (a random UUID given when the message is first created, kept on every retry),
so a retry of a message the server did commit is answered 409 and not recorded twice. 2xx and
409 count as delivered; 400, 413 and 422 are dropped for good (the reason is in the Error
Log); anything else is retried.

**Security incidents** (malware, intrusion, data exposure, targeted attacks) follow
[monitoring/security-incident-runbook.md](monitoring/security-incident-runbook.md): declare
the incident in Hostel360 (Ops Alert > Declare Security Incident), preserve evidence, and
report to CERT-In and ACGCET within six hours of detection. OCI alarms have their own
[runbook](monitoring/oci-alarm-runbook.md).

### Everyday commands

```
sudo journalctl -t h360-agent -t h360-backup -t h360-deploy --since today
sudo systemctl list-timers 'h360-*'
sudo h360-waf-ban list | ban <ip> | unban <ip>
sudo fail2ban-client status
sudo h360-2fa-enrol <user> | --reset <user>          # authenticator app enrolment (3.5)
sudo h360-site-init --trusted-networks               # campus addresses into Messaging Settings
sudo h360-snapshot --reason <why> [--keep-days N]     # ad-hoc volume backup (evidence: --keep-days 180)
sudo maldet --report list
sudo docker compose -p hostel360 --env-file /etc/hostel360/stack.env \
     -f /opt/hostel360/deploy/compose/compose.yml exec backend bench --site <site> console
```

### Host configuration files

| File | Contents | Example |
|---|---|---|
| `/etc/hostel360/stack.env` | compose settings, image tag, campus egress addresses (`CAMPUS_EGRESS_CIDRS`) | `compose/.env.example` |
| `/etc/hostel360/agent.env` | host agent, probe, ingest | `host/config/agent.env.example` |
| `/etc/hostel360/backup.env` | restic/rclone, volume backups | `host/config/backup.env.example` |
| `/etc/hostel360/secrets/` | database root password, restic password, site admin password, OCI notification token | written by Ansible / `h360-site-init` |
| `/etc/hostel360/waf/tls/` | internal certificate for LB re-encryption | Ansible |
| `/etc/hostel360/waf/lists/` | `denylist.conf` (fail2ban), `rate-exempt.conf` (campus egress addresses and `h360_waf_rate_exempt_cidrs`) | Ansible |

### End of contract

The exit (TR 22: export, deletion in the application, server decommission with
`h360-decommission`, cloud clean-up with `terraform/oci/exit-purge.sh`, and the CERT-In log
hold) is described step by step in [EXIT.md](EXIT.md).

---

## 5. Validation

The checks used for this directory, runnable on any machine with Docker:

```
docker compose -f deploy/compose/compose.yml --env-file deploy/compose/.env.example config --quiet
docker run --rm -v "$PWD":/mnt -w /mnt koalaman/shellcheck:v0.11.0 -x \
    deploy/host/bin/h360-* deploy/docker/entrypoint.sh deploy/docker/build.sh deploy/docker/bin/* \
    deploy/terraform/oci/exit-purge.sh deploy/tests/*/run.sh tests/load/run.sh
cd deploy/terraform/oci && docker run --rm -v "$PWD":/w -w /w hashicorp/terraform:1.16.6 fmt -check -recursive
cd deploy/terraform/oci && docker run --rm -v "$PWD":/w -w /w -e TF_DATA_DIR=/tmp/tf --entrypoint sh \
    hashicorp/terraform:1.16.6 -c 'terraform init -backend=false -input=false >/dev/null && terraform validate'
cd deploy/ansible && ansible-playbook --syntax-check site.yml && ansible-lint site.yml roles
deploy/docker/build.sh                        # local image for this machine's platform
```

Without a local Ansible, the syntax check and lint run in the tools image described below:

```
docker run --rm -v "$PWD/deploy:/work/deploy:ro" -w /work/deploy/ansible -e HOME=/tmp \
    -e ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections hostel360-deploy-checks:1 \
    sh -c 'ansible-playbook -i inventory.example.ini --syntax-check site.yml && ansible-lint site.yml roles'
```

Regression checks for the edge and the server configuration (Docker only; the tools image
carries Ansible, ansible-lint and fail2ban at the tested versions):

```
docker build -t hostel360-deploy-checks:1 -f deploy/tests/Containerfile deploy/tests
deploy/tests/waf/run.sh        # CSP and PayU at the WAF, CRS still blocking, in-stack 443/80 for PDFs
deploy/tests/ansible/run.sh    # campus egress checks and their WAF, fail2ban and stack.env outputs
deploy/tests/fail2ban/run.sh   # h360-auth counts failed sign-ins only, never throttled requests
deploy/tests/host/run.sh       # h360-ingest (message_id, retries, spool limits), uptime probe, Kuma relay,
                               # h360-decommission against a stand-in server
deploy/tests/exit/run.sh       # exit-purge.sh: order of the deletions, confirmations, log hold
```

Capacity (TR 15) is measured with the k6 load test in `tests/load/` against the staging stack;
see `tests/load/README.md` and the results kept in `tests/load/results/`.

## 6. Tender clauses covered here

| Clause | Requirement | How |
|---|---|---|
| 5.1 | Integrated with the official hostel website | `/integration/` pages and `links.php` menu feed (CORS for the website's origin) |
| 6.1–6.3 | 6 CPU, 12 GB RAM, 300 GB NVMe | 8 OCPU Ampere A1, 16 GB, 400 GB balanced block volume |
| 6.4 | 4,000,000 inodes | ext4 with one inode per 16 KB (~26 million), asserted by Ansible |
| 6.5 | 300 PHP workers | PHP-FPM pool `pm = ondemand`, `pm.max_children = 300` |
| 6.6 | Location: India | Hyderabad (primary), Mumbai (backups) |
| TR 15 | 1,500 concurrent users | k6 load test `tests/load/` (1,500 portal + 40 desk users, p95 < 800 ms, < 1 % errors, ~150 req/s) run on staging; results in `tests/load/results/` |
| TR 22 | Exit: data handed over, then deleted everywhere, with a certificate | [EXIT.md](EXIT.md): application purge, `h360-decommission`, `terraform/oci/exit-purge.sh`, CERT-In log hold |
| Security | Firewall, WAF, malware scanning, HTTPS, encrypted backups, CERT-In log retention and NTP, incident reporting within six hours | section 2, [security incident runbook](monitoring/security-incident-runbook.md) |
