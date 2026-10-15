# External monitoring

Three independent layers watch HyFib Hostel360; any one of them still works when the
others cannot:

| Layer | Runs in | Sees | Notifies |
|---|---|---|---|
| Hostel360 Operations (health checks, host agent, uptime probe) | the application and its server | application internals, host metrics, security signals | escalation matrix in Ops Alert Settings (e-mail, SMS, WhatsApp, webhooks) |
| OCI Monitoring alarms (`deploy/terraform/oci/monitoring.tf`) | OCI Hyderabad | the VM and load balancer from outside the VM | OCI Notifications topic: e-mail, PagerDuty, Slack, and the application |
| Uptime Kuma (this directory) | a small VM in OCI Mumbai | the public portal as a user sees it | its own channels (e-mail, Slack, PagerDuty, Telegram) |

## Uptime Kuma VM

- Shape: `VM.Standard.A1.Flex` with 1 OCPU / 6 GB (or `VM.Standard.E2.1.Micro`), Ubuntu 24.04,
  in ap-mumbai-1, public subnet. Ingress: 80 and 443 from anywhere (certificate issuance
  and the push endpoint), SSH only from the HyFib office addresses.
- A DNS name for the VM, e.g. `monitor.hyfib.com`.
- Install Docker Engine and the compose plugin, copy the `uptime-kuma/` directory, then
  `MONITOR_HOSTNAME=monitor.hyfib.com docker compose up -d`. Caddy obtains the certificate
  and publishes only `/api/push/...`; the Uptime Kuma interface listens on 127.0.0.1:3001,
  open it through `ssh -L 3001:127.0.0.1:3001 ubuntu@<vm>`.
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
