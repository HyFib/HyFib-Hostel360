# Exit runbook (TR 22)

At the end of the contract HyFib hands ACGCET a complete export of its data, deletes every
copy it holds once ACGCET confirms in writing that the export arrived, and issues a deletion
certificate. This runbook is the order in which that is done on the production installation.
The application part is described in detail in `hostel360/hostel_migration/README.md`
(sections 2 and 5); this page adds the server and the cloud.

Nothing here is reversible after step 2. Read it through once before starting, and keep the
sign-off sheet at the end filled in as you go.

| Step | Who | Where | Tool |
|---|---|---|---|
| 1. Export, check, hand over | HyFib operator | server | `bench h360-export`, `h360-verify-export` |
| 2. Delete the data in the application | HyFib operator | server | `bench h360-purge-institution-data` |
| 3. Prepare the CERT-In log hold | OCI tenancy administrator | workstation | `exit-purge.sh prepare-hold` |
| 4. Retire the server | HyFib operator | server | `h360-decommission` |
| 5. Destroy backups and infrastructure | OCI tenancy administrator | workstation | `exit-purge.sh execute` |
| 6. Outside OCI | HyFib operations | various | by hand |
| 7. After 181 days: release the log hold | OCI tenancy administrator | workstation | `exit-purge.sh release` |

## 0. Before starting

- ACGCET's written confirmation that it has received the export is required from step 2 on:
  its reference number, date and signatory. The deletion commands ask for the reference.
- Agree a date with ACGCET and tell students, parents and staff when the portal closes.
- Tell the HyFib on-call: from step 4 the OCI alarms, Uptime Kuma and the escalation matrix
  will report the portal as down. Pause the Uptime Kuma monitors (step 6 deletes them).
- The server is reached through the Bastion (deploy/README.md, section 3.3). In this runbook
  `B` stands for the backend container's bench, as root on the server:

  ```
  B="docker compose -p hostel360 --env-file /etc/hostel360/stack.env -f /opt/hostel360/deploy/compose/compose.yml exec backend"
  SITE=hostel.accet.ac.in
  ```

  The containers see the sites folder (`/srv/hostel360/sites` on the server) as
  `/home/frappe/frappe-bench/sites`. Everything the commands below write goes there, so it
  survives container restarts and is deleted in step 4; nothing is written into a
  container's own filesystem.

## 1. Export, check and hand over

```
install -d -o 1000 -g 1000 -m 0700 /srv/hostel360/sites/hostel360-exit
(umask 077 && openssl rand -base64 33 >/srv/hostel360/sites/hostel360-exit/export.pass)
chown 1000:1000 /srv/hostel360/sites/hostel360-exit/export.pass      # the containers' user
$B bench --site $SITE h360-export --output /home/frappe/frappe-bench/sites/hostel360-exit --encrypt \
   --passphrase-file /home/frappe/frappe-bench/sites/hostel360-exit/export.pass
$B bench --site $SITE h360-verify-export --passphrase-file /home/frappe/frappe-bench/sites/hostel360-exit/export.pass \
   --path /home/frappe/frappe-bench/sites/hostel360-exit/hostel360-export-<site>-<timestamp>.tar.gz.h360enc
```

The Exit Export Log must show the export as *Verified*. Copy the archive and its `.sha256`
file off the server through the Bastion and hand them to ACGCET; give the passphrase
separately (in person or by a different channel). Record ACGCET's written confirmation.

## 2. Delete the data in the application

Dry run first; it changes nothing and lists what will be deleted:

```
$B bench --site $SITE h360-purge-institution-data --confirm-site $SITE \
   --acknowledgement-reference "<ACGCET reference>" \
   --certificate-dir /home/frappe/frappe-bench/sites/hostel360-exit/certificate
```

Then the same command with `--execute` (it asks once more). It deletes every Hostel360 record,
the people's accounts, the uploaded files, Frappe's local backups and this server's copy of
the export, and writes the signed certificate `H360-DEL-<site>-<timestamp>.json` (with `.md`
and `.pdf`). It must finish with exit code 0 and a clean after-check; if it exits with 2, run
it again until it does.

Before going on:

- Copy the certificate to `/root` (step 4 reads it from there) and off the server to HyFib's
  records: `install -m 0600 /srv/hostel360/sites/hostel360-exit/certificate/H360-DEL-* /root/`.
- Put the site's `encryption_key` (in `/srv/hostel360/sites/$SITE/site_config.json`) into
  HyFib's vault with the exit records. It is the only way to verify the certificate once the
  site is gone (`bench h360-verify-deletion-certificate --path FILE --key-file KEY`); step 4
  checks that the vault copy is the right one.

## 3. Prepare the CERT-In log hold (tenancy administrator)

The CERT-In directions require logs to be kept for 180 days, in India. Deleting the server
and its backups would delete the server's logs early, so they are moved into a separate
bucket first. On the administrator's workstation, in `deploy/terraform/oci` with
Terraform's state available:

```
./exit-purge.sh plan                 # inventory; nothing changes
./exit-purge.sh prepare-hold         # <backup bucket>-exit-hold in ap-mumbai-1
```

The hold bucket is private, has a retention rule of 181 days (nothing in it can be changed or
deleted before then) and a lifecycle rule that deletes its contents a day after. The server
gets permission to add objects to it, not to change or delete them. If `terraform output
exit_inventory` is missing (a state written before this runbook existed), run
`terraform apply -refresh-only` once: it records the outputs and changes no resource.

## 4. Retire the server

On the server, as root. Make a passphrase for the log archive and put it in HyFib's vault
with the exit records (it is needed to read the logs if CERT-In asks for them):

```
(umask 077 && openssl rand -base64 33 >/root/exit-logs.pass)
h360-decommission --site $SITE --certificate /root/H360-DEL-<site>-<timestamp>.json \
   --log-passphrase-file /root/exit-logs.pass            # the plan; nothing changes
h360-decommission --site $SITE --certificate /root/H360-DEL-<site>-<timestamp>.json \
   --log-passphrase-file /root/exit-logs.pass --execute
```

Add `--export-path <path>` for any other copy of the export on this server. With
`--execute` it asks for the escrowed `encryption_key`, the site name, ACGCET's
acknowledgement reference and the word DESTROY, then:

1. puts the stack, host security and malware-scan logs, the journal and the containers' own
   logs into an AES-256 archive and uploads it, with a manifest and the certificate, to the
   hold bucket, and checks the upload;
2. stops the backup, restore-test, snapshot, agent, probe and scan timers, the scheduler
   and the workers;
3. drops the site without a backup (`bench drop-site --no-backup --force`) and removes the
   copy drop-site archives;
4. adds the last logs (the drop itself) to the hold, then removes the containers;
5. deletes the rest of the sites folder, the MariaDB data directory with its binary logs,
   Redis queue data, the pre-deploy and pre-restore dumps, restore-test and staging
   folders, local logs, ClamAV data, the restic cache, the ingest spool and state, the
   malware quarantine, the log passphrase file and `/etc/hostel360` with every secret.

It writes `/root/h360-decommission-<host>-<time>.json` and uploads it to the hold bucket;
step 5 looks for it there. If it stops part-way, fix the cause and run the same command
again: finished steps are not repeated. Copy the record off the server.

## 5. Destroy the backups and the infrastructure (tenancy administrator)

```
./exit-purge.sh execute --certificate ~/H360-DEL-<site>-<timestamp>.json
```

It checks that the server's decommission record is in the hold bucket (if the server could
not be reached, `--server-lost "<reason>"` records why), asks for the backup bucket's name,
ACGCET's acknowledgement reference and `DESTROY <name>`, then:

1. removes the server's access to the hold bucket;
2. deletes every object **version** and unfinished multipart upload in the backup bucket
   (the restic repository), which the server itself was never allowed to do;
3. runs `terraform destroy` for everything else (Terraform shows its plan and asks for
   `yes`). `prevent_destroy` on the bucket, the data volume, the load balancer's reserved
   address and the Vault key is lifted for this run only, by a temporary
   `exit_override.tf`; the `.tf` files are not edited. The OCI Logging logs (load balancer
   and VCN flow logs, CERT-In) and the Vault key are taken out of Terraform's state first;
4. deletes every block-volume and boot-volume backup of the server in both regions
   (scheduled ones, `h360-snapshot` ones and cross-region copies), after the destroy so the
   backup policy cannot add one, and waits until they are gone;
5. schedules the Vault key for deletion, then the vault (OCI keeps them for 30 days, then
   deletes them; nothing encrypted with them remains by then).

`plan` also lists any other backup in the compartment that it does not recognise as the
server's; delete those by hand if they are. If a subnet cannot be deleted while its flow
log exists, `execute` stops with only network resources left; `release` finishes them.
Every action is written to `~/hostel360-exit/actions.jsonl` with the operator and the time.

## 6. Outside OCI

- Uptime Kuma (Mumbai): delete the Hostel360 monitors, the push monitor, the API key, and
  the ingest relay with its secrets and spool
  (`docker compose rm -sf ingest-relay && docker volume rm hostel360-uptime_relay-spool`).
- DNS: ask ACGCET to remove the portal's record; remove the hostel website's links to
  `/integration/` (deploy/integration/website/README.md).
- Credentials that belong to ACGCET (Razorpay or PayU, MSG91 and the DLT portal, WhatsApp):
  tell ACGCET to remove HyFib's users and rotate the keys and webhook secrets.
- HyFib's own records: delete the Ansible vault file of this installation, the export
  passphrase (once ACGCET confirms it has decrypted the export) and any HyFib copy of the
  export; keep the certificate, the `encryption_key`, the decommission record, the log
  passphrase (until the release) and `~/hostel360-exit`.
- Terraform state: once `release` has run, delete the state object and its old versions in
  the state bucket (they hold resource identifiers and, if a PEM certificate was uploaded,
  the load balancer's private key).

## 7. Release the log hold (on or after the date `prepare-hold` printed)

```
./exit-purge.sh release
```

It refuses to run earlier. It deletes the hold bucket's contents, its retention and
lifecycle rules, the bucket and its policy, the OCI Logging logs and their log group, and
finishes any network resource left in step 5. Then delete the log passphrase from the vault.

## Sign-off sheet

The certificate lists the stores outside the application "still to be destroyed". Each is
signed off with the evidence named here.

| # | Certificate item | Done in | Evidence | Date | Signed |
|---|---|---|---|---|---|
| 1 | Site database and folder dropped without a backup; archived copy deleted | step 4 | decommission record: `dropped` | | |
| 2 | restic snapshots, object versions and unfinished uploads in the backup bucket | step 5.2 | `actions.jsonl`: "backup bucket emptied"; bucket gone | | |
| 3 | Block-volume and boot-volume backups in both regions, cross-region copies, ad-hoc snapshots | step 5.4 | `actions.jsonl`: "volume backups: none left" | | |
| 4 | Pre-deploy dumps and the restic cache | step 4 | decommission record: `deleted` | | |
| 5 | Export passphrase file and copies of the export outside the server | steps 4 and 6 | decommission record; HyFib vault entry removed | | |
| 6 | Redis data, with the containers | step 4 | decommission record: `down`, `deleted` | | |
| 7 | Logs under the CERT-In directions: kept until their expiry, then deleted | steps 3, 4, 7 | hold bucket manifest; `actions.jsonl` of `release` | | |
| – | Infrastructure (server, volumes, load balancer, network, IAM, alarms, Vault key) | step 5.3, 5.5 | `actions.jsonl`: "terraform destroy", key scheduled | | |
