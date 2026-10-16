# Backup and disaster recovery

Targets (tender 6.8, TR 8, TR 11; design §10): **RPO 24 hours**, **RTO 4 hours**, daily,
weekly and monthly encrypted backups in a second Indian region, a restore test every month,
and direct access to the backup repository for ACGCET.

The commands below run on the server as root. The scripts are in `deploy/host/bin/`; their
headers describe every option.

## 1. What is backed up

| Copy | Contents | Schedule | Retention | Where |
|---|---|---|---|---|
| restic database snapshot | `mariadb-dump --single-transaction --routines` of every site database (binlog position recorded) | daily 01:30 IST | 30 daily, 12 weekly (Sunday), 12 monthly (1st) | OCI Object Storage, Mumbai, versioned bucket |
| restic files snapshot | `sites/` (uploaded files, `site_config.json` with the encryption key), `/etc/hostel360`, MariaDB binary logs, stack and host security logs | daily 01:30 IST | same | same |
| Repository check and prune | `restic forget --prune`, `check --read-data-subset 5%` | Sundays | — | — |
| OCI volume backups | data volume and boot volume | daily incremental 02:30 IST, weekly and monthly full | 7 days, 4 weeks, 6 months | OCI Block Storage, Hyderabad (optional cross-region copy) |
| Pre-deploy / pre-restore copies | local dump, restic snapshot tagged `pre-deploy`/`pre-restore`, volume backup | every deployment and restore | local dumps: last 5; volume backups: 14 days (set as their OCI retention) | server, Mumbai, Hyderabad |

All copies are encrypted: restic encrypts with AES-256 before anything leaves the server;
OCI encrypts block and object storage at rest (optionally with a customer-managed key in
OCI Vault). The bucket is versioned and the server's own credentials cannot purge object
versions, so a compromised server cannot destroy its backups.

Every run reports an **Ops Backup Record** to Hostel360 (Daily, Weekly, Monthly, Restore
Test, Snapshot). A failure raises a Critical alert (`backup.<type>.failed`,
`host.backup.<host>`), and the health checks raise `backup.stale` if no backup has
succeeded for 26 hours. The Monthly Performance Report counts successful and failed backups
and quotes the last restore test.

## 2. Recovery objectives

| Objective | Target | How it is met |
|---|---|---|
| RPO | 24 hours | Nightly database and file snapshots. MariaDB binary logs are in each night's file snapshot, so a restore can be replayed forward to a point in time when the binlogs survive. |
| RTO | 4 hours | New server from Terraform and Ansible in about 1 hour; restore of the database and files in about 1 hour for the expected data size; DNS already points at the load balancer, which is recreated with its reserved public IP. |

## 3. Restoring

### 3.1 On the running server (data loss, wrong change, corruption)

```
sudo h360-restore list                            # snapshots (db and files)
sudo h360-restore restore                         # latest database + files, guided
sudo h360-restore restore --db <id> --db-only     # one database snapshot only
sudo h360-restore restore --files <id> --files-only
```

The tool shows the plan and asks for the site name; takes safety copies of the current state
(local dump, `pre-restore` restic snapshot, volume backup); downloads the chosen snapshot(s) to
a staging folder and checks them (the database dump must end with mariadb-dump's completion
line) while the site is still running, so a damaged snapshot or an Object Storage failure stops
the restore with nothing changed; then puts the site in maintenance mode and stops the
application containers; loads the databases from the staged dump (if that load fails, the
pre-restore copy is loaded back and the site stays in maintenance mode for checking) and only
then synchronises the sites directory; recreates database users from `site_config.json`;
starts the containers; runs `bench migrate` (the snapshot may predate the running release);
turns maintenance off and runs a smoke check. A `restore.<host>` alert records that a restore
happened. Every safety dump (pre-deploy and pre-restore) is accepted only when mariadb-dump
finished and the archive is intact; `deploy/tests/host/backup-safety.sh` checks this behaviour.

### 3.2 Disaster recovery on a new server (VM or region lost)

1. **Infrastructure.** `cd deploy/terraform/oci && terraform apply` (same `terraform.tfvars`;
   in a regional disaster, change `region` and the availability domain). The reserved
   public IP keeps the DNS record valid; otherwise update the A record to the new
   `lb_public_ip`.
2. **Server.** Run the Ansible playbook (`deploy/README.md` §3.3) with the same vault: it
   installs the stack, the host scripts, the backup configuration and the restic password.
3. **Data.** Start the stack with an empty sites directory, then restore the old server's
   snapshots: `sudo h360-restore restore --host <old host label>`.
4. **Check.** `curl -s https://<site>/api/method/hostel360.api.health.readiness`,
   `sudo h360-agent`, sign in to the desk, open the Operations Status page, take a fresh
   backup (`sudo h360-backup`).
5. **Record.** Raise a Support Ticket (category Outage) with the timeline; the outage and
   its duration appear in the Monthly Performance Report.

Keep with the recovery kit (HyFib operations vault, and a sealed copy with ACGCET): the
restic password, the Ansible vault password, the Terraform state bucket details, and the
site's `encryption_key` (also inside the file snapshots).

## 4. Monthly restore test

`h360-restore-test` runs automatically at 03:00 IST on the 2nd of every month. It loads the
latest database snapshot into a throwaway MariaDB container with no network and compares
every site database with the live one (number of tables, and rows in the core tables: users,
students, allotments, invoices, receipts). The result is an Ops Backup Record of type
*Restore Test*; a failure raises `host.restore-test.<host>` (Critical), and
`backup.restore_test.overdue` warns if no successful test has been recorded for 35 days.

To run it by hand, for example after a failed test was fixed:

```
sudo h360-restore-test && sudo journalctl -t h360-restore-test -n 50
```

Once a year (and after major changes) HyFib also rehearses a full disaster recovery on a
staging server (section 3.2) and records the elapsed time against the 4-hour RTO.

## 5. Direct backup access for ACGCET (TR 8)

ACGCET officials have read-only access to the backup bucket through the
`acgcet-backup-readers` group (Terraform `backup_reader_group_name`): they can list and
download objects, but not change or delete them. The restic password is escrowed with
ACGCET (sealed envelope or their own vault) at go-live (`deploy/README.md` §3.7, item 14);
without it no one can read the backups.

To verify or restore independently, on any Linux machine with `restic` and `rclone` and an
OCI API key of a user in that group:

```
export RCLONE_CONFIG_OOS_TYPE=oracleobjectstorage
export RCLONE_CONFIG_OOS_PROVIDER=user_principal_auth
export RCLONE_CONFIG_OOS_NAMESPACE=<namespace>
export RCLONE_CONFIG_OOS_COMPARTMENT=<compartment OCID>
export RCLONE_CONFIG_OOS_REGION=ap-mumbai-1
export RESTIC_REPOSITORY=rclone:oos:<bucket>/restic
restic snapshots                              # asks for the escrowed password
restic dump <snapshot id> <file in snapshot> > hostel360.sql
```

(`user_principal_auth` reads the API key from `~/.oci/config`.) HyFib assists with a test
restore on request at no cost.

## 6. What is not in the backups

- Redis (cache and job queue): rebuilt on start; queued jobs that had not run are re-created
  by the scheduler or retried by the senders.
- Container images: rebuilt from the tagged commit or pulled from OCIR.
- OCI Logging (LB access and VCN flow logs) has its own 180-day retention in OCI.
