# OCI alarm runbook

Each OCI Monitoring alarm (`deploy/terraform/oci/monitoring.tf`) links here. Every alarm
also reaches Hostel360 as an Ops Alert when the `alarm_https_endpoint` subscription is
configured, so acknowledge and resolve it there to keep the escalation matrix accurate.
Anything that looks deliberate (unknown processes, unexplained load, traffic from scanners)
is handled with the [security incident runbook](security-incident-runbook.md) instead.

Connect to the server through the Bastion first:

```
oci bastion session create-managed-ssh --bastion-id <bastion_id> \
  --target-resource-id <instance_id> --target-os-username ubuntu \
  --ssh-public-key-file ~/.ssh/id_ed25519.pub
```

## cpu

CPU above the threshold for 10 minutes.

1. `docker stats --no-stream` - which container is busy.
2. backend busy at a fee deadline or results day is expected; check the request rate in
   `/srv/hostel360/logs/waf/access.log` (`awk '{print $4}' access.log | cut -c2-18 | uniq -c | tail`).
3. A worker container busy for long: `docker logs hostel360-queue-long-1 --since 30m`
   (mess bill computation, bulk imports).
4. MariaDB busy: `/srv/hostel360/mariadb/slow.log`.
5. Unknown processes on the host: `top -c`; if suspicious, treat it as a security incident
   and follow the [security incident runbook](security-incident-runbook.md) (declare it in
   Hostel360, preserve evidence, report to CERT-In and ACGCET within 6 hours).

## memory

1. `docker stats --no-stream` - containers near their limit are restarted by Docker when
   they exceed it; check `h360-agent` restart alerts.
2. `free -m` and `swapon --show`: sustained swap use means the memory plan needs review
   (deploy/README.md, "Memory plan").
3. ClamAV signature reloads briefly raise memory; this clears within minutes.

## heartbeat

The VM stopped sending metrics (Oracle Cloud Agent silent).

1. OCI console: instance state. If STOPPED, start it; the stack starts with the
   `hostel360-stack` service.
2. If RUNNING but unreachable, check the serial console history and the `accessibility`
   alarm; reboot from the console if hung (data is on the block volume, InnoDB recovers).
3. After recovery: `systemctl status hostel360-stack`, `h360-agent`, and confirm the
   portal with `h360-deploy --status` and the readiness URL.

## accessibility

OCI cannot reach the VM's network interface. Same steps as heartbeat; the instance
availability configuration restores it automatically after an infrastructure event.

## lb-unhealthy

The load balancer's health check (`/api/method/hostel360.api.health.liveness` on port
8443) fails: users see errors.

1. `docker compose -p hostel360 ps` (as root, with `--env-file /etc/hostel360/stack.env
   -f /opt/hostel360/deploy/compose/compose.yml`) - all services running and healthy?
2. `curl -sk --resolve <site>:8443:127.0.0.1 https://<site>:8443/api/method/hostel360.api.health.readiness`
3. WAF: `docker logs hostel360-waf-1 --since 15m`; a bad configuration shows at startup.
4. Firewall: `iptables -S DOCKER-USER` must allow 8443 from the load balancer subnet.
5. If a deployment just happened, `h360-deploy --rollback`.

## lb-5xx

Server errors returned to users.

1. Hostel360 desk > Error Log, newest first.
2. `docker logs hostel360-backend-1 --since 15m`.
3. 502/504 from the WAF: the frontend or backend container is restarting; see `docker ps`.
4. Database errors: `docker logs hostel360-mariadb-1 --since 15m`, disk space (`df -h /srv/hostel360`).
