# Security incident runbook

For anything that may be a cyber security incident on HyFib Hostel360: malware found by
ClamAV or Linux Malware Detect, a burst of WAF blocks or failed sign-ins that looks targeted,
a process or account nobody can explain, an unexpected change to the portal, data seen where
it should not be, a denial of service, or an attack on the payment pages.

The CERT-In directions of 28 April 2022 (section 4(ii)) require a reportable incident to be
reported to CERT-In **within six hours of being noticed**, and the Hostel360 contract gives
the institution the same deadline. The clock starts at detection, not at confirmation: report
what is known within six hours and send the details later.

## Contacts

| Role | Who | How |
|---|---|---|
| Incident owner (decides, reports, signs off) | HyFib Director, Santhanakrishnan Manikandan | manikandan.s@hyfib.com, +91 97413 80530 |
| Deputy incident owner (acts when the owner cannot be reached within 30 minutes) | HyFib technical lead, escalation level 2 in Ops Alert Settings | named, with e-mail and mobile, at go-live ([deploy/README.md](../README.md), section 3.7) |
| First responder, 24×7 | HyFib helpdesk, escalation level 1 | support@hyfib.com, +91 96060 60596 |
| Institution | the ACGCET official named at go-live (Hostel Administrator or the Principal) | recorded at go-live, section 3.7, and on each incident as "Institution Contact Informed" |
| CERT-In | Indian Computer Emergency Response Team | incident@cert-in.org.in, or the incident reporting form, phone and fax numbers on www.cert-in.org.in |

Whoever notices the incident first tells the helpdesk and the incident owner at once, by
phone, and declares it in Hostel360 (step 1). If neither the owner nor the deputy answers,
the helpdesk carries on with steps 1 to 5; reporting late is worse than reporting incomplete.

## 1. Declare (minutes 0-15)

In Hostel360, as a HyFib Operations user:

- From the Ops Alert that showed the problem: **Declare Security Incident**. This opens a
  Security Incident (`SINC-YYYY-#####`) with the alert's first-seen time as **Detected On**.
- Without an alert: Security Incident > New. Set **Detected On** to when the incident was
  first noticed (not to now if it was noticed earlier).
- Fill in the CERT-In category, severity, affected systems and what was seen. Tick
  **Personal Data Involved** if students' or parents' data may have been exposed.

**Report By** shows the six-hour deadline. The health checks raise a Warning alert four hours
after detection and a Critical one at six hours while either report is still missing.

## 2. Contain, without destroying evidence

Take the least disruptive step that stops the harm, and write each one with its time in
**Actions Taken**:

- One client or range: `sudo h360-waf-ban ban <address>` (refused with 403 at the WAF).
- A compromised account: untick Enabled on the user in the desk, then end its sessions:
  `bench --site <site> execute frappe.sessions.clear_sessions --kwargs '{"user": "<user>", "force": true}'`;
  reset its two-factor key with `sudo h360-2fa-enrol --reset <user>` once the owner is reached.
- The portal itself: `bench --site <site> set-maintenance-mode on`.

`bench` here means, on the server as root:
`docker compose -p hostel360 --env-file /etc/hostel360/stack.env -f /opt/hostel360/deploy/compose/compose.yml exec backend bench`.
- A leaked credential: rotate it now (step 7 lists them), not after the investigation.

Do **not** reboot the server, recreate containers, run `h360-deploy`, restore a backup or
delete suspicious files before step 3: each of these destroys evidence. Linux Malware Detect
has already quarantined what it found; leave the quarantine as it is.

## 3. Preserve evidence (within the first hour)

On the server, as root, into a folder only root can read:

```
id=SINC-2026-00001                                   # the incident's name
dir=/srv/hostel360/evidence/${id}; install -d -m 0700 "${dir}"; cd "${dir}"
date -Is > collected-at.txt; chronyc tracking > clock.txt
ps auxwwf > processes.txt; ss -tupan > sockets.txt; who -a > logins.txt; last -Faiw > last.txt
docker ps -a --no-trunc > containers.txt
for c in $(docker ps -q --filter label=com.docker.compose.project=hostel360); do
  docker inspect "$c" > "inspect-$c.json"; docker logs --timestamps "$c" > "log-$c.txt" 2>&1
done
journalctl --since "-2 days" -o short-iso > journal.txt
tar -czf logs.tar.gz /srv/hostel360/logs /var/log/audit /var/log/auth.log* /var/log/syslog* \
    /var/log/fail2ban.log* /var/log/ufw.log* /usr/local/maldetect/logs 2>/dev/null
maldet --report list > maldet-reports.txt 2>&1
sha256sum * > SHA256SUMS
h360-snapshot --reason "$(tr 'A-Z' 'a-z' <<<"${id}")" --keep-days 180   # volume backup kept 180 days
```

Also export from the desk (Report view > Export) the Ops Alerts, Error Log, Access Log and
Activity Log entries of the period, and attach them to the incident. Copy the evidence folder
off the server to HyFib's incident store (in India) and record where it is in **Actions
Taken**. Logs are kept for 180 days in any case (CERT-In); evidence of an incident is kept
until the incident is closed and for as long as CERT-In or the institution asks.

## 4. Report to CERT-In (before **Report By**)

E-mail incident@cert-in.org.in (or use the reporting form on www.cert-in.org.in) with:

- organisation: HyFib Solutions Private Limited, for ACGCET Hostels (HyFib Hostel360),
  contact: the incident owner's name, e-mail and mobile;
- time of occurrence (if known) and time of detection, in IST;
- CERT-In category (as on the incident) and a short description;
- systems affected: the portal URL, the server's region (OCI ap-hyderabad-1), operating
  system (Ubuntu 24.04), the addresses and accounts involved;
- what has been done so far (steps 2 and 3) and whether personal data may be involved.

Record the time sent as **Reported to CERT-In On** (or press **Reported to CERT-In Now**) and
CERT-In's acknowledgement number as **CERT-In Reference**.

## 5. Inform the institution (before **Report By**)

Phone the ACGCET contact and confirm by e-mail with the same facts. Record **Reported to
Institution On** and **Institution Contact Informed** (name and designation). With both
reports recorded the incident becomes *Reported* and the deadline alert stops.

## 6. Personal data (DPDP Act 2023)

ACGCET is the Data Fiduciary for students' and parents' data and HyFib processes it on
ACGCET's behalf. When **Personal Data Involved** is ticked:

- HyFib tells ACGCET at once (step 5 does this) and gives it everything it needs to notify:
  what data, whose, since when, likely consequences, and what is being done;
- ACGCET informs the Data Protection Board of India without delay and each affected student
  or parent in plain language; the Board expects the detailed report within 72 hours (DPDP
  Rules, 2025). HyFib drafts both for ACGCET and records the dates in **Actions Taken**.

## 7. Eradicate and recover

- Find how it happened before rebuilding; fix that first (patch, rule, configuration).
- Clean or restore: `sudo h360-restore list`, then a restore from a snapshot older than the
  first sign of compromise (deploy/README.md, "Backups and restore").
- Rotate what may have been seen: the ingest secret (Ops Alert Settings and `agent.env`),
  the OCI notification token, the MariaDB root password, the Administrator password, payment
  gateway keys and webhook secrets (Razorpay, PayU), SMS and WhatsApp tokens, OCI auth tokens
  and API keys, the Uptime Kuma API key; for a compromised desk account also its two-factor key.
- Watch closely for a week: Ops Alerts, WAF blocks, failed sign-ins.

## 8. Close

When it is contained, recovered and both reports are made: write the **Assessment Notes**
(cause, impact, what changes), set **Closed On** and close the incident. Hold a review within
five working days and update this runbook if a step was missing or wrong. Security events
appear in the Monthly Performance Report.

**Not reportable.** If the assessment shows it was not a reportable incident (a false
positive, a blocked scan with no effect), clear **Reportable to CERT-In** and explain why in
the Assessment Notes; the incident becomes *Not Reportable*. When in doubt, report.
