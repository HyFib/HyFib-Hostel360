# External monitoring

Three independent layers watch HyFib Hostel360; any one of them still works when the
others cannot:

| Layer | Runs in | Sees | Notifies |
|---|---|---|---|
| Hostel360 Operations (health checks, host agent, uptime probe) | the application and its server | application internals, host metrics, security signals | escalation matrix in Ops Alert Settings (e-mail, SMS, WhatsApp, webhooks) |
| OCI Monitoring alarms (`deploy/terraform/oci/monitoring.tf`) | OCI Hyderabad | the VM and load balancer from outside the VM | OCI Notifications topic: e-mail, PagerDuty, Slack, and the application |
| Uptime Kuma (this directory) | a small VM in OCI Mumbai | the public portal as a user sees it | its own channels (e-mail, Slack, PagerDuty, Telegram); its readiness results also reach Hostel360 as `uptime_external` samples |

Runbooks: [OCI alarms](oci-alarm-runbook.md) and [security incidents](security-incident-runbook.md)
(CERT-In and institution reporting within six hours).

## Uptime Kuma VM

- Shape: `VM.Standard.A1.Flex` with 1 OCPU / 6 GB (or `VM.Standard.E2.1.Micro`), Ubuntu 24.04,
  in ap-mumbai-1, public subnet. Ingress: 80 and 443 from anywhere (certificate issuance
  and the push endpoint), SSH only from the HyFib office addresses.
- A DNS name for the VM, e.g. `monitor.hyfib.com`.
- The clock must be synchronised (systemd-timesyncd or chrony, as on Ubuntu by default):
  the ingest API refuses messages more than 300 seconds off.
- Install Docker Engine and the compose plugin and copy the `uptime-kuma/` directory. In it:
  `cp .env.example .env && chmod 0600 .env`, set `MONITOR_HOSTNAME` and `H360_INGEST_URL`,
  then `docker compose up -d uptime-kuma caddy`. Caddy obtains the certificate and
  publishes only `/api/push/...`; the Uptime Kuma interface listens on 127.0.0.1:3001, open
  it through `ssh -L 3001:127.0.0.1:3001 ubuntu@<vm>`. Create the monitors below, then the
  ingest relay (last section).
- Back up the `uptime-kuma-data` volume (Settings > Backup export) after configuring it.

## Monitors

| Monitor | Type | Target | Interval | Alert when |
|---|---|---|---|---|
| Portal readiness | HTTP(s) - Keyword | `https://<site>/api/method/hostel360.api.health.readiness`, keyword `"ok"` | 60 s | 3 failures in a row |
| Portal login page | HTTP(s) | `https://<site>/login` | 300 s | 2 failures |
| Student portal | HTTP(s) | `https://<site>/hostel` | 300 s | 2 failures |
| Integration pages | HTTP(s) - Keyword | `https://<site>/integration/health.php`, keyword `ok` | 300 s | 2 failures |
| Certificate | (option on the readiness monitor) | certificate expiry notification | daily | 21 days before expiry |
| Application heartbeat | Push | Push URL from Uptime Kuma | 5 min | no push for 15 min |
| DNS | DNS | `<site>` A record = load balancer IP | 1 h | record changes |

The **Application heartbeat** is a dead-man switch: put the Push URL
(`https://monitor.hyfib.com/api/push/<token>?status=up`) in Hostel360 > Ops Alert Settings >
Heartbeat URL. The application calls it after every health-check run
(every 5 minutes); if the scheduler, the workers or the whole application stop, the pushes
stop and Uptime Kuma raises the alarm even though nothing inside Hostel360 can.

Notification channels: HyFib helpdesk e-mail (support@hyfib.com), the on-call PagerDuty
service, and the operations Slack channel. Use the same escalation as the application
(L1 helpdesk immediately, L2 technical lead after 2 hours, L3 Director for any outage).

## Ingest relay (`uptime_external`)

The on-host probe (`h360-uptime-probe`) runs on the server it measures, so when that VM or
the Hyderabad region is down nobody inside it records the outage, and the monthly report can
only call those minutes "unmonitored". The `ingest-relay` service reads the **Portal
readiness** monitor from Uptime Kuma's metrics once a minute and sends the results to
Hostel360 as `uptime_external` samples (1 up, 0 down or pending; nothing during a Kuma
maintenance window), which the availability calculation prefers over every other source.

While the portal cannot be reached the readings wait in the `relay-spool` volume (35 days)
and are delivered, oldest first, when it answers again; each message carries a fixed
`message_id`, so a retry of one that did arrive is not counted twice.

Setup, once the monitors exist:

1. Uptime Kuma: Settings > API Keys > Add; no expiry. Save the key as
   `secrets/kuma_api_key`.
2. The ingest secret (`vault_h360_ingest_secret` in the Ansible vault; on the server it is
   `H360_INGEST_SECRET` in `/etc/hostel360/agent.env` and the Ingest Secret in Ops Alert
   Settings): save it as `secrets/h360_ingest_secret`. When it is rotated, update this file
   too.
3. `chmod 0700 secrets && chmod 0400 secrets/*`, then `docker compose up -d` (the relay
   starts only once both files exist).
4. Check: `docker compose run --rm ingest-relay python3 -I /opt/h360/h360-kuma-relay.py --once`
   prints the reading and `"spool_empty": true`; in Hostel360, Ops Metric Sample shows
   `uptime_external` rows from host `uptime-kuma-mumbai` within five minutes.

`KUMA_MONITOR` in `.env` must match the monitor's name exactly; if it does not, the relay logs
"monitor ... is not in Uptime Kuma's metrics" once an hour and records nothing.
