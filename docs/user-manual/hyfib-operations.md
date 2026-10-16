# HyFib operations manual

For the **HyFib helpdesk and engineers** (role *HyFib Operations*): the desk screens you use
to watch and support the ACGCET installation. What every alert means and the procedures
behind it are in the [operations runbook](../OPERATIONS-RUNBOOK.md); server work is in
`deploy/README.md`.

Your workspace is **HyFib Operations**: shortcuts to *Operations Status*, *Alerts* and
*Support Tickets*; number cards for open critical alerts, open alerts, open tickets and failed
backups in the last 30 days; charts of alerts per day and by severity; links to Security
Incidents, metrics, backup records, the monthly reports, Ops Alert Settings, Messaging
Settings, the Message Log, online payment transactions and the Gateway Reconciliation report.
You cannot see student records, invoices, receipts or grievances; online payment
transactions are visible read-only for payment support.

## 1. Operations Status

*HyFib Operations > Operations Status*: every health check (database, Redis cache and queue,
job queues and failed jobs, scheduler, disk, application errors, failed logins, payments,
messaging, backups, restore tests, TLS certificate, host agents, escalation contacts, monthly
report, security incidents) with its status, message and figures; core components marked;
open alerts by severity; the last backup and the last health-check run. **Refresh** runs the
checks again, read-only. Links go to Alert History, Uptime and Incidents, Support Tickets,
Monthly Performance Reports and Alert Settings.

## 2. Alerts

*HyFib Operations > Alerts*. Each alert has a key, title, severity, category, source,
first/last seen, occurrence count, escalation level and who has been notified.

| Action | When |
|---|---|
| **Acknowledge** | As soon as you start on it: stops the hourly reminders of a Critical alert and shows others you have it. Escalation to L2/L3 still follows the clock until it is resolved. |
| **Resolve** | When fixed, with notes. Health-check, host-agent, OCI and probe alerts resolve themselves when the condition clears. |
| Suppress | (API `ops.suppress`) for a known noisy alert: occurrences keep counting, nobody is notified, until resolved. |
| **Declare Security Incident** | When the alert may be a cyber security incident: opens a Security Incident with the alert's first-seen time (section 5). |

The same alert is never duplicated: repeats of the same key raise the occurrence count; a key
that resolves and comes back within 15 minutes re-opens the same alert quietly.

## 3. Ops Alert Settings

*HyFib Operations > Alert Settings* (HyFib Operations and System Manager can edit; the
institution's administrator can read).

1. **General**: enabled, environment (Production/Staging/Development), instance label,
   minutes between reminders for unacknowledged Critical alerts (60).
2. **Escalation Levels**: L1 helpdesk (0 minutes), L2 technical lead (120 minutes, outage
   immediately), L3 Director (180 minutes, outage immediately), each with e-mails, mobiles,
   the minimum severity they receive and the minimum severity and channel for SMS/WhatsApp.
   Fill in **L2's contacts at go-live**: until then a Warning alert says the matrix has a
   level without contacts.
3. **Channels**: team destinations — e-mail list, SMS, WhatsApp, Slack, Microsoft Teams,
   Google Chat or a generic webhook — each with a minimum severity.
4. **Report Recipients**: e-mail addresses of the ACGCET officials who receive the Monthly
   Performance Report (required; Warning alert while empty).
5. **Thresholds**: disk, database latency, job backlog and failures, scheduler lag, error
   spike, failed logins, TLS expiry, payment failures and pending time, message failure
   percentage, backup age, restore-test age, agent silence, ticket response and resolution
   hours. Change them only with a reason recorded in the alert or ticket that prompted it.
6. **Integrations**: the **ingest secret** (shown on request; rotate it here and then in
   `/etc/hostel360/agent.env` and the Uptime Kuma relay), the **OCI notification token**
   (part of the OCI subscription URL), and the **Heartbeat URL** (Uptime Kuma push).

Buttons: **Send Test Notification** (to every level and channel), **Run Health Checks Now**.

## 4. Support tickets

*HyFib Operations > Support Tickets*. Institution staff raise tickets from *Get Support*; you
also enter tickets for calls and e-mails. Each ticket opens an alert, so you are paged.

| Status action | Meaning |
|---|---|
| **Acknowledge** | First response (within 1 hour). A reply comment from you counts too. |
| **Start Work** | In Progress |
| **Waiting on Institution** | The resolution clock pauses until the institution answers |
| **Resolve** | With resolution details (within 4 hours) |
| **Confirm and Close** / **Re-open** | Done by the person who raised it (or by you on their confirmation) |

The form shows the response and resolution due times and flags a breached SLA. Assign the
ticket to the engineer working on it (*Assigned To*). An *Outage* ticket is Critical and pages
L2 and L3 at once. Send the onsite engineer when a ticket cannot be fixed remotely within the
SLA, and record the visit on the ticket.

## 5. Security incidents

*HyFib Operations > Security Incident*: title, CERT-In category, severity, whether it is
reportable and whether personal data is involved, the linked alert, **Detected On**,
**Report By** (detected + 6 hours), the times reported to CERT-In and to the institution with
the CERT-In reference and the institution contact, description, affected systems, actions
taken and assessment notes.

Follow the [security incident runbook](../../deploy/monitoring/security-incident-runbook.md).
A Warning alert fires 4 hours after detection and a Critical one at 6 hours while either
report is missing. With both reports recorded the status becomes *Reported*; close it with
the assessment when recovered, or clear *Reportable to CERT-In* with the reason if it turns
out not to be reportable.

## 6. Monthly Performance Report

*HyFib Operations > Monthly Performance Report*. Produced on the 1st at 07:00 for the
previous month (`MPR-YYYY-MM`), submitted and e-mailed as PDF to the Report Recipients with a
copy to the helpdesk.

1. On the 1st, open the report: uptime (with its source and unmonitored minutes), incidents
   (listed with durations), CPU, memory and disk, security events, malware detections, WAF
   blocks, backups and the last restore test, tickets and SLA compliance, active students,
   portal sign-ins, online payments.
2. **Refresh Figures** recomputes them (for example after late metrics arrived); write the
   **Summary** for the institution.
3. If it was not sent (no recipients, mail problem): fix the cause and **Send to Recipients**
   (or **Send Again**). It must reach the institution **by the 6th**; the health checks retry
   hourly and the alert becomes Critical on the 4th.
4. Present it at the monthly review meeting in the first week ([runbook §8](../OPERATIONS-RUNBOOK.md#8-monthly-governance)).

## 7. Other records

| Record | Use |
|---|---|
| **Ops Backup Record** | Every backup, restore test and snapshot reported by the server: type, status, size, snapshot ID, duration, message |
| **Ops Metric Sample** | Time series from health checks, the host agent and uptime probes (kept 400 days) |
| **Message Log** | Delivery of SMS, WhatsApp, e-mail and in-app messages (texts hidden; recipients masked); confidential-grievance messages hidden |
| **Messaging Settings** | Gateway configuration with the administrator; **Send Test Message** |
| **Online Payment Transaction** | Payment attempts and their gateway status (read only) |
| **Gateway Reconciliation** | Online payments against receipts |
| **Alert History**, **Uptime and Incidents** | Reports for the review meeting and audits |

## 8. Daily checklist

1. Operations Status green, or every amber/red item has an acknowledged alert.
2. No Critical alert unacknowledged; tickets within SLA.
3. Last night's backup *Success*; restore test on the 2nd.
4. Message failures and payment reconciliation clean.
5. Hand over open items to the next shift in the alert or ticket notes.
