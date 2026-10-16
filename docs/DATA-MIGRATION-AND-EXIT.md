# Data migration and exit

Two tender obligations, both run with bench commands from the Hostel Migration module:

- **TR 5** — about 30 GB and 10 years of legacy data migrated without loss, encrypted in
  transit and at rest, and retrievable.
- **TR 22** — at the end of the contract, all data and files handed over in open formats at
  no cost, then deleted everywhere after ACGCET confirms receipt in writing, with a deletion
  certificate.

The reference for every option, the mapping format and the transforms is
[`hostel360/hostel_migration/README.md`](../hostel360/hostel_migration/README.md); the
server and cloud steps of the exit are in [`deploy/EXIT.md`](../deploy/EXIT.md). This page
is the procedure.

On the production server, `bench` means the backend container's bench, as root:

```
B="docker compose -p hostel360 --env-file /etc/hostel360/stack.env -f /opt/hostel360/deploy/compose/compose.yml exec backend"
SITE=hostel.accet.ac.in
```

## 1. Legacy data migration (TR 5)

### 1.1 Plan (implementation days 8–18)

| Step | Who | Output |
|---|---|---|
| Full export of the existing system (database dump and files) transferred encrypted (SFTP through the Bastion, or an encrypted archive) | ACGCET IT with HyFib | Source folder on the server under the site's `private/migration/` |
| One CSV or XLSX file per legacy table, with a heading row; scanned documents and photos in `attachments/` | HyFib | Source files |
| Field-by-field mapping agreed with ACGCET | HyFib, ACGCET hostel office | `mapping.json` (start from `hostel_migration/samples/sample_mapping.json`) |
| Dry runs until the run is *Completed* and the reconciliation *Matched* | HyFib | Reconciliation report (Excel, Markdown, JSON) |
| The real run | HyFib | Migration Run record and reports |
| Sample checks and sign-off | ACGCET | Signed migration sign-off; the source copy is kept until then |

### 1.2 Commands

```
# rehearsal: everything validated and written in one transaction, reports produced, then rolled back
$B bench --site $SITE h360-migrate --source /home/frappe/frappe-bench/sites/$SITE/private/migration/2026-11 \
   --mapping /home/frappe/frappe-bench/sites/$SITE/private/migration/mapping.json --dry-run

# only some entities while fixing them
$B bench --site $SITE h360-migrate --source ... --mapping ... --dry-run --entity students --entity guardians

# the real run (same command without --dry-run)
$B bench --site $SITE h360-migrate --source ... --mapping ... --title "ACGCET hostel register, final"
```

Exit codes: `0` success; `1` failure or refusal; `2` finished but something needs attention
(failed rows or a reconciliation mismatch).

From the desk, a Hostel Administrator can do the same with **Migration Run** (*Hostel Admin >
Data Migration*): give the folder name relative to `private/migration/`, paste the mapping,
**Start**; progress shows on the form and the reports are attached when it finishes; **Dry
Run Again** and **Migrate for Real** copy the run.

### 1.3 How completeness is proved

- **Every row accounted for.** Each row is validated by the normal DocType rules inside its
  own savepoint; a failing row is rolled back alone and listed with its file line, legacy ID
  and reason. Rows left out on purpose by a mapping `filter` are counted as *Excluded*.
- **Reconciliation** at the end of every run (dry runs too): counts per entity (source rows −
  excluded = records in Hostel360 from that entity), money totals (invoice totals and
  outstanding, receipts, mess bills) from the source column against the target field, and
  the SHA-256 of every attachment against the bytes stored. *Completed* only when nothing
  failed and everything matched.
- **Idempotent.** Every migrated row is recorded in **Migration Record Map** (source system
  + entity + legacy ID → record, with a checksum of the mapped values). Running again
  skips unchanged rows, updates changed ones and creates new ones, so a final delta extract
  just before go-live is safe. `source_system` keeps two legacy applications' IDs apart.
- **Historical data.** During a run `frappe.flags.hostel360_migration` is set: past dates,
  supplied statuses and paid amounts are accepted, no SMS/WhatsApp/e-mail is sent and no
  payment gateway is called.
- **Encryption.** In transit through the Bastion (SSH) or as an encrypted archive; at rest on
  the encrypted block volume, in the private files folder, and in the encrypted backups.

### 1.4 After go-live

Keep the reconciliation report (Excel and Markdown) with the signed sign-off. The Migration
Run and Record Map stay in Hostel360 (System Manager, Hostel Administrator, HyFib Operations
read access) as the audit trail of where every record came from.

## 2. Exit (TR 22)

Order of work; nothing is reversible after step 3. Full commands: `deploy/EXIT.md`.

| Step | Who | Command | Result |
|---|---|---|---|
| 1. Agree the exit schedule and the portal closing date with ACGCET; pause monitors | HyFib, ACGCET | — | Exit plan |
| 2. Export, verify, hand over | HyFib operator | `bench --site $SITE h360-export --output <dir> --encrypt --passphrase-file <file>` then `bench --site $SITE h360-verify-export --path <archive> --passphrase-file <file>` | Exit Export Log *Verified*; archive + `.sha256` to ACGCET, passphrase by a separate channel |
| 3. ACGCET confirms receipt in writing | ACGCET | — | Reference number, date, signatory |
| 4. Delete the data in the application | HyFib operator | `bench --site $SITE h360-purge-institution-data --confirm-site $SITE --acknowledgement-reference "<ref>"` (dry run), then with `--execute` | Signed deletion certificate `H360-DEL-<site>-<time>.json` (+ `.md`, `.pdf`) |
| 5. CERT-In log hold | OCI tenancy administrator | `deploy/terraform/oci/exit-purge.sh prepare-hold` | Hold bucket with a 181-day retention rule |
| 6. Retire the server | HyFib operator | `h360-decommission --site $SITE --certificate <json> --log-passphrase-file <file> --execute` | Logs archived to the hold, site dropped without backup, local data and secrets deleted |
| 7. Destroy backups and infrastructure | OCI tenancy administrator | `exit-purge.sh execute --certificate <json>` | Backup bucket versions, volume backups, all resources destroyed; Vault key scheduled for deletion |
| 8. Outside OCI | HyFib operations | by hand | Uptime Kuma monitors, DNS, ACGCET's gateway and messaging users rotated |
| 9. After 181 days | OCI tenancy administrator | `exit-purge.sh release` | Log hold and OCI logs deleted |

### 2.1 What the export contains

| Path | Content |
|---|---|
| `data/csv/<DocType>.csv` | Every Hostel360 DocType, all columns; child tables in their own files; settings as field/value pairs; UTF-8 with BOM so Excel shows Tamil correctly |
| `data/xlsx/<DocType>.xlsx` | The same, one workbook per DocType with a sheet per child table |
| `data/core/` | Users (no password data) and roles, the file index, the audit trail and comments of Hostel360 records |
| `database/<db>.sql.gz` | Complete MariaDB dump, restorable with `bench --site SITE restore FILE` |
| `files/public/`, `files/private/` | All uploaded files |
| `manifest.json`, `README.txt` | Site, apps and versions, row counts, size and SHA-256 of every file |

With `--encrypt` the archive is AES-256-GCM in 4 MiB chunks with a scrypt key from a
passphrase of at least 12 characters; any change, reordering or truncation is detected. The
institution can verify and decrypt it without Hostel360 (`h360-verify-export` needs no site;
the README includes a short Python decryptor). HyFib assists with moving the data to the
platform ACGCET chooses and supports a parallel check, at no cost.

### 2.2 Safeguards of the deletion

- `--confirm-site` must repeat the site; `--acknowledgement-reference` is required.
- The newest **verified** export of the site, including files, must be at most 30 days old
  (`--force-without-export` overrides only this, and the certificate says so).
- Without `--execute` nothing is deleted; with it the command asks once more at a terminal.
- The database deletion is one transaction; if it fails nothing is deleted and the site stays
  in maintenance mode. A run interrupted after the database step finishes the files when run
  again.

### 2.3 The certificate

Records the site, institution, acknowledgement reference, times, operator and host, the
export it relied on (with the manifest and archive SHA-256), rows deleted per DocType and per
related record type, accounts, files and bytes, the after-check, what was kept and why (the
Exit Export Log, HyFib Operations accounts and Administrator, CERT-In logs until their
180-day expiry), and the stores outside the application still to be destroyed, each signed
off on the sheet in `deploy/EXIT.md`. It is signed with HMAC-SHA256 under a key derived from
the site's `encryption_key`, kept in HyFib's vault so the certificate can be verified after
the site is gone:

```
bench h360-verify-deletion-certificate --path H360-DEL-<site>-<time>.json --key-file <key file>
```
