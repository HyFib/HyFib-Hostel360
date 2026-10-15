# Operations runbook

How HyFib supports and runs Hostel360 for ACGCET Hostels: the support model, who is called
when, what every alert means and what to do about it, and the routine and monthly work. The
server-level procedures (deploy, restore, firewall, WAF) are in `deploy/README.md`; this
document links to them rather than repeating them.

Related runbooks: [security incidents](../deploy/monitoring/security-incident-runbook.md)
(CERT-In and institution within six hours), [OCI alarms](../deploy/monitoring/oci-alarm-runbook.md),
[backup and DR](BACKUP-AND-DR.md), [exit](../deploy/EXIT.md).

## 1. Support model

| Service level | Commitment (bid document 11 §4, TR 10–11, TR 17–18) | How it is tracked |
|---|---|---|
| Server uptime | 99.9 % per month, excluding scheduled maintenance announced in writing | Monthly Performance Report (`uptime_percent`, measured every minute) |
| Support hours | 24×7×365 | Helpdesk rota; alerts page L1 at any hour |
| Response time | Within 1 hour of every reported issue | Support Ticket `response_due_by`, `response_sla_breached` |
| Resolution time | Within 4 hours | Support Ticket `resolution_due_by` (paused while *Waiting on Institution*), `resolution_sla_breached` |
| Backup | Daily, weekly and monthly, encrypted, in a second Indian region | Ops Backup Record; `backup.stale` alert |
| Onsite support | An engineer goes to ACGCET Hostels, Karaikudi, at no cost when an issue cannot be fixed remotely within the SLA | Support Ticket timeline |

**Helpdesk channels**: phone **+91 96060 60596**, e-mail **support@hyfib.com**, and the
online ticket form (desk: *Hostel Admin > Get Support*, which creates a Support Ticket).
Every ticket opens an Ops Alert (`support.ticket.<TKT>`), so tickets page the helpdesk
through the same channels and escalation as technical alerts.

## 2. Escalation matrix

| Level | Who | Contact | When |
|---|---|---|---|
| L1 | HyFib helpdesk | +91 96060 60596, support@hyfib.com | Every alert and ticket, 24×7, immediately |
| L2 | HyFib technical lead (named at kick-off) | entered in Ops Alert Settings at go-live | Not resolved within 2 hours (120 minutes); immediately for an outage |
| L3 | Santhanakrishnan Manikandan, Director | +91 97413 80530, manikandan.s@hyfib.com | Not resolved within 3 hours (180 minutes), or immediately for any outage |

The matrix lives in **Ops Alert Settings > Escalation Levels** and is installed with these
defaults; L2 ships without a contact and the health checks raise
`config.escalation.contacts` (Warning) until one is entered. Each level has a minimum
severity (L1 Info, L2 and L3 Warning) and receives SMS/WhatsApp only for Critical alerts
(`mobile_min_severity`); e-mail goes for everything the level accepts. The **channels**
table adds team destinations (Slack, Microsoft Teams, Google Chat, generic webhook, an
operations mailbox) with their own minimum severity.

An **outage** is an alert with *Is Outage* set: the uptime probe's "Portal unreachable",
critical OCI load-balancer or health-check alarms, a data volume not mounted, an `Outage`
support ticket, or any OCI alarm subscribed with `?outage=1`. Outages notify L2 and L3 at
once.

Unacknowledged Critical alerts are repeated every `critical_renotify_minutes` (60).
Acknowledging stops the reminders but not the time-based escalation; resolving stops both.

## 3. Daily operation

- **Operations Status** page (desk: *HyFib Operations > Operations Status*): every health
  check with its last result, open alerts by severity, the last backup and the last health
  check run. The same data is at `/api/method/hostel360.api.health.status` for HyFib
  Operations and Institution Auditors.
- **HyFib Operations** workspace: open alerts, open critical alerts, open tickets, failed
  backups in 30 days, alerts per day and by severity.
- The helpdesk on duty keeps the Operations Status page open, acknowledges every new alert
  within the response SLA, writes what they did in the alert's resolution notes and
  resolves it — or lets the system resolve it when the cause clears (health checks and the
  host agent resolve their own keys).

### Working an alert

1. Open the alert (link in the notification). **Acknowledge** it: the people already paged
   see that someone has it.
2. Read *Description* and *Details* (the check's measurements, the host, the record).
3. Follow the action for its key in the catalogue below.
4. **Resolve** with notes when fixed. Alerts raised by health checks, the host agent, OCI
   and the uptime probe resolve themselves when the condition clears; resolve by hand only
   what the system cannot see (for example a one-off failed backup that was re-run).
5. A noisy alert that cannot be fixed straight away can be **suppressed** (`ops.suppress`):
   occurrences keep counting, nobody is notified, until it is resolved.
6. Anything that may be a cyber security incident: **Declare Security Incident** on the
   alert and follow the [security incident runbook](../deploy/monitoring/security-incident-runbook.md).

## 4. Alert catalogue

Severity: I = Info, W = Warning, C = Critical. `<host>` is the server's host label,
`<PTX>` a Hostel Payment Transaction, `<TKT>` a Support Ticket, `<SINC>` a Security
Incident. Thresholds come from Ops Alert Settings and can be tuned there.

### 4.1 Application health checks (source *Health Check*, every 5 minutes)

| Key | Sev. | Raised when | What to do |
|---|---|---|---|
| `health.database.latency` | W / C | `select 1` takes longer than 500 ms (C at 2 s) | `docker stats`; MariaDB slow log `/srv/hostel360/mariadb/slow.log`; long-running jobs (`docker logs hostel360-queue-long-1 --since 30m`); disk I/O. |
| `health.redis.cache` | C | Redis cache does not answer | `docker compose ps redis-cache`; restart the container (`docker compose up -d redis-cache`); cache is disposable. |
| `health.redis.queue` | C | Redis job queue does not answer | Same for `redis-queue` (AOF persistence: never delete its volume). Jobs resume after restart. |
| `health.jobs.backlog` | W / C | Over 500 jobs waiting (C at 2,000), or no workers running (C) | `docker compose ps` for `queue-short`/`queue-long`; restart workers; look for a job flooding a queue (bulk import, publish dues). |
| `health.jobs.failed` | W / C | 10 or more new failed jobs since the last check (C at 50) | Desk: *RQ Job* list and the Error Log; fix the cause, then requeue if the work must run. |
| `health.scheduler` | C | Scheduler disabled, or the minute job has not run for 10 minutes | `bench --site <site> scheduler status` / `enable`; `docker compose ps scheduler`; check `pause_scheduler` / maintenance mode left on after a deploy. |
| `capacity.disk.sites` | W / C | Site file system 80 % full (C at 90 %) | `df -h /srv/hostel360` and `du -sh /srv/hostel360/*`; uploads, logs and pre-deploy dumps (only the last five are kept) are the usual growth; grow the block volume in the OCI console and the file system online. |
| `health.errors.spike` | W / C | 25 or more Error Log entries in 15 minutes (C at 100) | Error Log grouped by method (*Details* lists the top five); recent deploy? roll back with `h360-deploy --rollback` if it started with a release. |
| `security.failed_logins` | W / C | 30 or more failed desk logins in 15 minutes (C at 120) | *Details* lists the top addresses; confirm fail2ban has banned them (`fail2ban-client status`); `h360-waf-ban ban <ip>`; if targeted, declare a security incident. |
| `payments.pending_stale` | W | Online payments Pending for more than 30 minutes | Open each transaction, **Check with Gateway**; if the gateway is unreachable see `payment-gateway-unreachable`. |
| `payments.failures` | W / C | 10 or more failed online payments in an hour (C at 30) | Gateway status page; failure reasons on the transactions; a bank or UPI outage is outside our control — inform the hostel office. |
| `messaging.failure_rate` | W / C | 20 % or more of messages failed in the last hour (C at 50 %), at least 10 attempts | Message Log filtered by Failed; provider balance, DLT template rejections, WhatsApp token expiry. See also `messaging-*-failing`. |
| `backup.stale` | C | No successful backup in 26 hours (or none ever) | `journalctl -t h360-backup --since yesterday`; run `sudo h360-backup`; see [BACKUP-AND-DR.md](BACKUP-AND-DR.md). |
| `backup.restore_test.overdue` | W | No successful restore test in 35 days | Run `sudo h360-restore-test` now; it is scheduled for the 2nd of each month. |
| `security.tls.expiry` | W / C | Public certificate expires within 21 days (C within 7 days, or fails verification) | Renew the LB certificate (OCI Certificates or `lb_certificate_*` in Terraform) and apply. |
| `agent.heartbeat.<host>` | W | The host agent has been silent for 10 minutes | `systemctl list-timers 'h360-*'`, `journalctl -t h360-agent -n 50`; clock skew over 300 s makes ingest refuse the agent. |
| `config.escalation.contacts` | W | An escalation level has no e-mail and no mobile, or the matrix is empty | Fill in Ops Alert Settings > Escalation Levels (L2 at go-live). |
| `reports.monthly.unsent` | W / C | Last month's performance report has not reached the institution (C from the 4th; due by the 6th) | Check Report Recipients and the outgoing e-mail account; the health check retries hourly; or open the report and **Send to Recipients**. |
| `reports.monthly.no_recipients` | W | The report was produced but Ops Alert Settings has no Report Recipients | Add the institution officials' e-mails; send the report. |
| `security.incident.<SINC>.reporting` | W / C | A reportable incident 4 hours after detection (C at 6 hours) without both the CERT-In and the institution report recorded | Report now ([runbook](../deploy/monitoring/security-incident-runbook.md) steps 4–5) and record the times on the incident. |
| `health.check.<check>` | – | A health check itself failed to run (status unknown, not notified) | Error Log entry "health check … failed"; usually a code or migration problem. |

When every core check (database, Redis, job queue, scheduler, disk) is healthy the
application calls the **Heartbeat URL** (Uptime Kuma push). If those calls stop, Uptime
Kuma raises the alarm from outside.

### 4.2 Payments (source *App*, category Payments)

| Key | Sev. | Raised when | What to do |
|---|---|---|---|
| `payment-gateway-unreachable` | W | Reconciliation cannot reach the gateway (network, credentials, outage, throttling) | Outbound HTTPS from backend/workers (`deploy/README.md` "Outbound connections"); **Test Connection** in Payment Settings; gateway status page. Resolves itself on the next good run. |
| `payments-daily-reconciliation` | W | The 23:30 run found issues: a successful payment without exactly one receipt, a refund, an extra capture | *Gateway Reconciliation* report; fix each transaction (Check with Gateway, Mark Refunded, cancel/amend the receipt). |
| `payment-order-unreadable-<PTX>` | W | The gateway answers but cannot read this order (for example a test order after switching to live keys) | Check the gateway dashboard; **Close as Expired** on the transaction if nothing was paid. |
| `razorpay-return-signature`, `payu-return-signature` | W | A payment response with an invalid signature/hash was rejected | Usually a wrong key or salt after a change; repeated ones from many addresses may be tampering — consider a security incident. |
| `checkout-signature-<PTX>` | W | A checkout confirmation for this transaction failed signature verification | Compare with the gateway dashboard; never receipt by hand without the gateway confirming. |
| `payment-verification-<PTX>` | W | A captured payment did not match its order (amount or order) | Gateway dashboard; refund the payment if it does not belong; record the outcome on the transaction. |
| `payment-multiple-captures-<PTX>` | W | More than one captured payment on one order | The extra one is recorded but never receipted: refund it from the gateway dashboard, then **Mark Refunded**. |
| `payment-unallocated-<PTX>` | W | An online payment exceeds the dues it was meant for | The excess sits as unallocated on the receipt: allocate it to other dues or refund it. |
| `payment-refunded-<PTX>` | W | The gateway reports a refund for a receipted payment | Cancel or adjust the receipt so dues and reports are right (partial refunds are alerted but cannot be recorded against a receipt automatically). |
| `payment-authorised-not-captured-<PTX>` | W | An authorised payment could not be captured (the request had already closed) | The gateway returns it automatically; confirm on the dashboard and inform the payer if they call. |
| `razorpay-webhook-signature` | W | A Razorpay webhook with an invalid signature was rejected | Webhook secret in Razorpay Dashboard and Payment Settings must match. |
| `razorpay-webhook-not-configured`, `payu-webhook-not-configured` | W | A webhook arrived but the webhook secret / PayU key and salt are not set | Complete Payment Settings; resolves on the next verified delivery. |
| `payment-webhook-error-<PTX>` | W | A verified webhook could not be processed | Error Log for the transaction; **Check with Gateway**; resolves when a later delivery succeeds. |

### 4.3 Messaging, security and data jobs (source *App* or *Security*)

| Key | Sev. | Raised when | What to do |
|---|---|---|---|
| `messaging-msg91-failing`, `messaging-generic-http-failing`, `messaging-whatsapp-cloud-api-failing`, `messaging-email-queue-failing` | W / C | A provider failed `failure_alert_threshold` (5) times in a row (C at four times that); the circuit breaker pauses it | Provider dashboard (balance, DLT scrubbing, token expiry); **Send Test Message** in Messaging Settings; failed messages are retried with back-off and from Message Log > **Retry Now**. |
| `malware-detected-<hash>` | C | ClamAV found malware in an uploaded file; the file and every record pointing to it were deleted | Declare a security incident; find the uploader (in *Details*); check for other uploads by the same account. |
| `malware-scanner-unreachable` | W | clamd cannot be reached; uploads are accepted unscanned and queued | `docker compose ps clamav`; signature reload can take minutes after a restart. Queued files are scanned when it is back and the alert resolves. |
| `security.ingest.invalid_signatures` | W | 20 or more ingest requests with a bad signature in 15 minutes | Agent configured with an old secret after a rotation, or someone probing; compare `H360_INGEST_SECRET` in `/etc/hostel360/agent.env` with Ops Alert Settings. |
| `people:close-ended-allotments` | W | The daily job could not vacate some allotments past their end date | Error Log; vacate them from the Room Board; beds stay occupied until then. |
| `support.ticket.<TKT>` | by priority (Outage C) | A Support Ticket was raised, re-opened, or breached its SLA | Work the ticket (section 6). Resolving the ticket resolves the alert. |

### 4.4 Host, backup and deployment (source *Host Agent*, *Uptime Probe*, *Security*)

Sent by the host scripts through `api.ops.ingest`; the agent resolves what it opened when
the condition clears.

| Key | Sev. | Raised when | What to do |
|---|---|---|---|
| `host.uptime.<host>` | C, outage | The portal's liveness URL has not answered for 3 minutes (repeated hourly while down) | Check from outside (Uptime Kuma), then `systemctl status hostel360-stack`, `docker compose ps`, WAF logs; OCI console for the instance and LB. |
| `host.cpu.<host>` | W | CPU high over the interval | [OCI alarm runbook, cpu](../deploy/monitoring/oci-alarm-runbook.md#cpu). |
| `host.memory.<host>` | W / C | Memory (and swap) use high | [memory](../deploy/monitoring/oci-alarm-runbook.md#memory); containers at their limit restart. |
| `host.load.<host>` | W | 5-minute load above the CPU count | Same as CPU. |
| `host.disk.data.<host>`, `host.disk.root.<host>` | W / C (C, outage, when the data volume is not mounted) | Disk or inodes filling up | `df -h`, `df -i`; grow the volume; never run the stack on the boot volume. |
| `host.docker.<host>` | C | Docker engine not responding | `systemctl status docker`; restart Docker; the stack restarts with it (live-restore). |
| `host.container.<service>.<host>` | W / C | A container is unhealthy or down | `docker compose ps`, `docker logs <container> --since 30m`; `docker compose up -d <service>`. |
| `host.restart.<container>.<host>` | W | A container keeps restarting | Logs of the container; out-of-memory kills show in `dmesg`. |
| `host.fail2ban.<host>` | W | fail2ban not running | `systemctl restart fail2ban`. |
| `host.bans.<host>` | W | Many addresses banned at once | `h360-waf-ban list`; a campus address banned means `h360_campus_egress_cidrs` is wrong; a burst from many addresses may be an attack. |
| `host.waf.<host>` | W | Unusual number of WAF blocks | `/srv/hostel360/logs/waf/error.log` by rule id; a false positive after a release needs an exclusion (`deploy/waf/`); an attack, a security incident. |
| `host.clamav.<host>` | W / C | ClamAV signatures old or missing | `docker logs hostel360-clamav-1` (freshclam); outbound HTTPS to `database.clamav.net`. |
| `host.maldet.<host>` | W | Linux Malware Detect monitor not running | `systemctl status maldet`; restart it. |
| `host.maldet.<host>.<scan>` | C | maldet found infected files (quarantined) | Security incident; `maldet --report <scan>`. |
| `host.tls.internal.<host>` | W / C | The internal WAF certificate (LB re-encryption) is close to expiry | Re-run the Ansible `app` role to renew it. |
| `host.time.<host>` | W | Clock not synchronised | `chronyc tracking`; NTP egress to NIC/NPL servers. CERT-In requires synchronised clocks. |
| `host.reboot.<host>` | I | A reboot is pending after security updates | Schedule a reboot in a quiet hour (announce it if it exceeds a few minutes). |
| `host.backup.<host>` | C | `h360-backup` failed | `journalctl -t h360-backup`; re-run; [BACKUP-AND-DR.md](BACKUP-AND-DR.md). |
| `host.backup.partial.<host>` | W | The backup skipped some files | The log lists them; usually files removed during the run. |
| `backup.daily.failed`, `backup.weekly.failed`, `backup.monthly.failed`, `backup.snapshot.failed`, `backup.restore_test.failed` | C | An Ops Backup Record with status Failed was received | As above; a successful record of the same type resolves it. |
| `host.restore-test.<host>` | C | The monthly restore test failed | `journalctl -t h360-restore-test`; fix and re-run `sudo h360-restore-test`. |
| `restore.<host>` | W | Data was restored on the server | Expected during a restore; otherwise investigate who ran it. |
| `deploy.<host>` | C on failure, I on success | `h360-deploy` failed and rolled back / succeeded | On failure: `journalctl -t h360-deploy`, fix and redeploy; the site is running the previous release. |

### 4.5 OCI Monitoring (source *OCI Monitoring*)

| Key | Sev. | Raised when | What to do |
|---|---|---|---|
| `oci.alarm.<id>` | from the alarm | An OCI alarm fired (CPU, memory, metrics absent, accessibility, LB unhealthy backends, LB 5xx) | [OCI alarm runbook](../deploy/monitoring/oci-alarm-runbook.md); resolves when OCI sends the OK. |

## 5. Security incidents

Declare from the alert (**Declare Security Incident**) or *Security Incident > New*. The
six-hour clock runs from *Detected On*; the incident form shows *Report By*. Follow
[the security incident runbook](../deploy/monitoring/security-incident-runbook.md): contain
without destroying evidence, preserve evidence, report to CERT-In (incident@cert-in.org.in)
and to the ACGCET contact, handle personal-data aspects with ACGCET as Data Fiduciary,
recover, and close with an assessment and a review within five working days.

## 6. Support tickets

1. Institution staff raise tickets from the desk (*Get Support*) or by phone/e-mail; the
   helpdesk enters phoned and e-mailed ones as tickets.
2. **Acknowledge** (first response; a HyFib comment on the ticket counts too) within 1 hour.
3. **Start Work**; set **Waiting on Institution** while the answer depends on ACGCET (the
   resolution clock pauses).
4. **Resolve** with resolution details within 4 hours; the raiser confirms with **Confirm
   and Close** or **Re-open** with what is still wrong.
5. An issue that cannot be fixed remotely within the SLA: send the onsite engineer and
   record the visit on the ticket.

## 7. Routine tasks

| When | Task | How |
|---|---|---|
| Every shift | Review open alerts and tickets; nothing Critical left unacknowledged | Operations Status, HyFib Operations workspace |
| Daily | Backup succeeded, reconciliation clean, no stuck jobs | Ops Backup Record, `payments-daily-reconciliation`, Operations Status |
| Daily | Messages delivering | Message Log failed count; `messaging.failure_rate` |
| Weekly (Sunday, automatic) | restic repository check and prune | `h360-backup` timer; result in the journal |
| Weekly | Review WAF blocks and fail2ban bans for false positives | `/srv/hostel360/logs/waf/error.log`, `h360-waf-ban list` |
| Weekly | Review pip-audit and Trivy results of the security workflow | GitHub Actions *Security* run summary |
| Monthly (2nd, automatic) | Restore test | Ops Backup Record "Restore Test"; investigate any failure the same day |
| Monthly (1st, automatic) | Performance report produced | Check it on the 1st (section 8) |
| Monthly | Apply OS security updates that need a reboot; announce if needed | `host.reboot.<host>` |
| Monthly | Check the OCI cost and limits; certificate expiry dates | OCI console; `security.tls.expiry` |
| Quarterly | Rotate the ingest secret and the OCI notification token | Ops Alert Settings (rotate), then `agent.env` and the Kuma relay secret, then the OCI subscription URL |
| Quarterly | Access review: desk users, roles, hostel assignments, disabled leavers | User list with roles; Hostel Staff `is_active` |
| Each release | Changelog, image build, deploy in a quiet hour, smoke test | [DEPLOYMENT.md](DEPLOYMENT.md), `CHANGELOG.md` |
| Academic year start | New academic year (**Make Current**), fee structures, publish dues, bulk import of new students, allotments | Hostel Admin and People and Rooms workspaces |

### Common procedures

| Need | How |
|---|---|
| A student or parent locked out after wrong codes | Desk: Hostel Student / Hostel Guardian > **Unlock Sign-in** (after confirming who is calling) |
| A message did not arrive | Message Log for the recipient (masked); **Retry Now**; Messaging Settings test mode must be off in production |
| A payment shows Pending | Transaction > **Check with Gateway**; never receipt online money by hand |
| Re-run health checks now | Ops Alert Settings > **Run Health Checks Now** |
| Test the alert channels | Ops Alert Settings > **Send Test Notification** |
| Deploy, roll back, check status | `sudo h360-deploy <tag>`, `--rollback`, `--status` (`deploy/README.md` §4) |
| Console on the server | `docker compose -p hostel360 --env-file /etc/hostel360/stack.env -f /opt/hostel360/deploy/compose/compose.yml exec backend bench --site <site> console` |

## 8. Monthly governance

| What | When | Who | How |
|---|---|---|---|
| Monthly Performance Report | Produced automatically at 07:00 on the 1st for the previous month; delivered to the institution **by the 6th** (TR 13) | HyFib operations | Open *Monthly Performance Report* `MPR-YYYY-MM`; check the figures (**Refresh Figures** recomputes them before sending), write the *Summary*; it is e-mailed as PDF to Report Recipients with a copy to the helpdesk. If it did not go out: **Send to Recipients**. `reports.monthly.unsent` turns Critical on the 4th. |
| Performance review meeting | In the **first week** of every month (TR 12, TR 16) | ACGCET officials with an authorised HyFib technical representative | Walk through the report: uptime and unmonitored minutes, incidents and their causes, resource use against capacity, security events, malware detections and WAF blocks, backups and the restore test, ticket SLA compliance, portal use and online payments; agree actions and record them in the minutes. |
| Security and performance audits | Monthly, at ACGCET's request (TR 14) | HyFib with ACGCET's auditors | Read-only access through the Institution Auditor role (Operations Status, Alert History, Uptime and Incidents, reports) and the backup bucket; HyFib supplies logs and evidence on request. |
| Customisations from the To-Be study | Tracked at each review (TR 6) | HyFib | Change Request tickets; delivered at no additional cost. |

The report covers: uptime percentage and downtime minutes (with the measurement source
and unmonitored minutes), incidents and critical alerts with durations, average and peak
CPU and memory, disk use, security events, malware detections, WAF blocks, backups
succeeded and failed, the last restore test, tickets raised and resolved with SLA
compliance, active students, portal sign-ins, and online payments (count and amount).
