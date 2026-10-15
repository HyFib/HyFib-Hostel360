# Hostel Migration

Legacy data migration (tender TR 5), the exit export and the deletion of the institution's
data after exit (TR 22), demonstration/UAT data and non-interactive site setup for HyFib
Hostel360.

| Tool | Command | Desk |
|---|---|---|
| Load legacy data | `bench --site SITE h360-migrate --source DIR --mapping FILE [--dry-run] [--entity NAME ...]` | **Migration Run** |
| Exit export | `bench --site SITE h360-export --output DIR [--no-files] [--encrypt] [--passphrase-file FILE]` | **Exit Export Log** |
| Check an export | `bench [--site SITE] h360-verify-export --path DIR_OR_ARCHIVE [--passphrase-file FILE] [--extract-to DIR]` | **Exit Export Log** |
| Delete the data after exit | `bench --site SITE h360-purge-institution-data --confirm-site SITE --acknowledgement-reference REF [--execute]` | **Exit Export Log** |
| Check a deletion certificate | `bench [--site SITE] h360-verify-deletion-certificate --path FILE.json [--key-file FILE]` | – |
| Demo / UAT data | `bench --site SITE h360-demo-data [--students 60] [--seed 2026]` | – |
| New site setup | `bench --site SITE h360-setup --institution-name ... --short-name ... --admin-email ... --portal-url ...` | – |

Exit codes: `0` success, `1` failure or refusal, `2` finished but something needs attention
(failed rows, a reconciliation mismatch, demo records that could not be created, items left
after a deletion).

---

## 1. Migrating legacy data

### 1.1 How a migration is done

1. Export every legacy table to its own **CSV** (UTF-8 or Windows "CSV") or **XLSX** file,
   one row per record with a heading row. Put scanned documents and photos in an
   `attachments/` folder next to them.
2. Write a mapping (section 1.3). Start from `samples/sample_mapping.json`.
3. Run a **dry run**. Every row is validated and written inside one database transaction,
   the reconciliation report is produced, and then everything is rolled back:

   ```
   bench --site hostel.example.ac.in h360-migrate --source /srv/legacy/2026-06 \
         --mapping /srv/legacy/mapping.json --dry-run
   ```

4. Fix the source files or the mapping until the dry run reports **Completed** and
   **Matched**. Each failed row is listed with its file line, its legacy ID and the reason.
5. Run the same command without `--dry-run`. Keep the reconciliation report (Excel and
   Markdown) with the migration sign-off.
6. Running again is always safe (section 1.4): a later extract only updates what changed.

From the desk, a Hostel Administrator can create a **Migration Run**, give the folder name
(relative to the site's `private/migration/` folder, the only place desk runs may read
from), paste the mapping, and start it. The run is processed in the background; the form
shows progress and the reports are attached when it finishes. Additional readable folders
can be allowed with `"hostel360_migration_sources": ["/srv/legacy"]` in `site_config.json`.

Only one migration runs at a time on a site (a database lock enforces it). A run that was
interrupted by a server restart is marked *Failed* when someone chooses **Run Again**.

### 1.2 What a run does

* Entities are loaded in the mapping's `load_order`, so that lookups (a student's
  programme, an allotment's bed) refer to records already migrated.
* Each row is mapped, validated by the normal DocType controllers, inserted or updated,
  and (when `submit` is set) submitted. `frappe.flags.hostel360_migration` is set for the
  whole run: controllers accept past dates and the statuses and amounts supplied, never
  call payment gateways and send **no** SMS, WhatsApp or e-mail.
* Every row runs in its own savepoint. A failing row is rolled back alone; the run
  continues. Real runs commit every 100 rows and after each entity.
* After the last entity the reconciliation is computed (section 1.5).

### 1.3 Mapping format

A mapping is one JSON object:

```json
{
  "version": 1,
  "name": "ACGCET hostel register, June 2026 extract",
  "source_system": "acgcet-hostel-register",
  "defaults": {
    "date_formats": ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"],
    "datetime_formats": ["%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M:%S"],
    "null_values": ["NULL", "N/A", "-"],
    "encoding": "utf-8-sig",
    "delimiter": ","
  },
  "attachments_dir": "attachments",
  "load_order": ["hostels", "rooms", "students"],
  "entities": { "hostels": { ... }, "rooms": { ... }, "students": { ... } }
}
```

| Key | Meaning |
|---|---|
| `version` | Always `1`. |
| `name` | Shown on the Migration Run and in reports. |
| `source_system` | Short, stable, lower-case name of the legacy application. Migration Record Map entries are kept per source system, so two legacy applications (say the hostel register and the mess software) can both have a `students` table with an ID `1`. **Never change it between runs of the same source**, or rows will be treated as new. Defaults to a slug of `name`. |
| `defaults` | `date_formats` / `datetime_formats` (Python `strptime` codes) tried in order; `null_values`: cell texts treated as empty (default `NULL, NONE, N/A, NA, NIL, -, --`); `encoding` (default: UTF-8, then Windows-1252); `delimiter` (default: detected from `, ; tab \|`). |
| `attachments_dir` | Folder (inside the source folder) holding attachments. Default `attachments`. |
| `load_order` | Every entity, dependencies first. A lookup to an entity that loads later is rejected. |

#### Entities

```json
"students": {
  "file": "students.csv",
  "doctype": "Hostel Student",
  "key": "stud_id",
  "match_on": ["register_number"],
  "filter": {"column": "deleted", "not_in": ["Y"]},
  "fields": { "register_number": {"column": "reg_no", "transforms": ["strip", "upper"], "required": true} }
}
```

| Key | Meaning |
|---|---|
| `file` | CSV or XLSX file name in the source folder. `sheet` picks an XLSX sheet (default: the first). `encoding` / `delimiter` / `date_formats` / `datetime_formats` override the defaults for this file. |
| `doctype` | Target DocType (for `mode: "child"`, the parent DocType). |
| `key` | Column, or list of columns, that identify the legacy row. Composite keys are joined with `\|`. Must be unique within the file; at most 140 characters. |
| `fields` | Target field name → field specification (below). |
| `match_on` | Target fields used to adopt an existing record the first time a row is seen (e.g. a hostel already created by hand, a guardian shared by two siblings). Without it a row with no Migration Record Map entry always creates a new record. |
| `filter` | Leave rows out on purpose: `{"column": "...", "in": [...]}`, `"not_in"`, `"equals"` or `"not_empty": true/false`. Excluded rows are counted as *Excluded*, not as failures. |
| `children` | Child tables filled from the same row (section "Child rows"). |
| `submit` | Submit the document after saving (allotments, invoices, receipts). |
| `on_submitted_change` | When a row changes after its record was submitted: `"fail"` (default) reports the row so that someone cancels and amends it by hand; `"skip"` leaves the submitted record as it is. |
| `docname` | Optional field specification for the record name, to keep legacy numbers (`{"column": "receipt_no"}`). Use only where the DocType does not number records itself. |
| `reconcile` | Money totals to compare (section 1.5): `[{"label": "Fee invoices total", "source_column": "total", "target_field": "total_amount", "transforms": ["decimal"]}]`. |
| `mode` | `"document"` (default) or `"child"` (section "Child entities"). |

#### Field specifications

A field is either a column name (`"hostel_name": "name"`) or an object with exactly one
source:

| Key | Meaning |
|---|---|
| `column` | Source column (headings match without regard to case). |
| `columns` + `separator` | Several columns joined (default separator: a space), e.g. address lines. |
| `value` | A constant (`{"value": "Validated"}`). |
| `template` | Text built from other mapped fields or columns: `"{room}-{bed_no}"`. Names are looked up among this entity's mapped fields first, then among the source columns. Templates are filled after all plain fields, in any order. |

and optionally:

| Key | Meaning |
|---|---|
| `transforms` | List of transforms applied in order (below). |
| `value_map` | Legacy code → Hostel360 value, matched without regard to case: `{"BOYS": "Male", "GIRLS": "Female"}`. A code mapped to `""` becomes empty. |
| `strict` | With `value_map`: a value not in the map fails the row (recommended for statuses and types). |
| `lookup` | Resolve a legacy ID to a migrated record: `"students"` / `{"entity": "students"}`, or to an existing record by a field: `{"doctype": "Hostel Fee Head", "field": "fee_head_name"}`. Add `"required": false` to leave the field empty instead of failing when nothing matches. |
| `default` | Used when the cell is empty (after `null_values`). Defaults are not transformed. |
| `required` | Fail the row when the final value is empty. |
| `attachment` | The cell holds a path inside `attachments_dir`; the file is stored as a private File on this field and its SHA-256 verified (section 1.5). |

Special target field: **`creation`** sets the record's created-on date and time to the
legacy value (when a grievance was raised, a leave was applied for), so reports by date
remain right.

#### Transforms

Transforms run only on non-empty values.

| Transform | Result |
|---|---|
| `strip`, `collapse_spaces`, `upper`, `lower` | Whitespace and case clean-up. |
| `title` | `ARUN KUMAR` → `Arun Kumar`, `k.arun` → `K.Arun`; words already in mixed case are kept. |
| `split_name` (`part`: `first` / `last`) | First word / everything after it: `Arun Kumar R` → `Arun` / `Kumar R`. |
| `replace` (`old`, `new`), `prefix` / `suffix` (`value`), `digits`, `truncate` (`length`) | Text edits. |
| `date` (`formats`, `excel_serial`) | To `YYYY-MM-DD`. Tries `formats`, then the entity's, then the mapping's `date_formats`. Excel date cells are read directly; `excel_serial: true` also accepts serial numbers such as `45458`. |
| `datetime` (`formats`) | To `YYYY-MM-DD HH:MM:SS`; a date without a time is midnight. |
| `decimal` (`places`, default 2; `non_negative`) | Indian amounts: `Rs. 12,500/-`, `₹ 1,00,000.50`, `(450.00)`, `450 Dr` (negative). Rounded half-up. |
| `integer` | Whole numbers; `4.5` fails. |
| `boolean` (`true`, `false`: extra words) | `Y/Yes/True/1/Active/On` → 1, `N/No/False/0/Inactive/Off` → 0. |
| `mobile` | 10-digit Indian mobile through the application's own `normalize_mobile` (`+91 98765 43210`, `09876543210`, `91-9876543210`). |
| `email` | Lower-case, checked. |
| `pincode` | 6-digit PIN code, spaces removed. |

A transform is written as its name (`"upper"`) or as an object with parameters
(`{"name": "date", "formats": ["%m/%d/%Y"]}`).

#### Child rows

```json
"children": {
  "items": [
    {"$when": "rent", "fee_head": {"value": "Hostel Rent"}, "amount": {"column": "rent", "transforms": ["decimal"]}},
    {"$when": "deposit", "fee_head": {"value": "Caution Deposit"}, "amount": {"column": "deposit", "transforms": ["decimal"]}}
  ]
}
```

Each object is one possible child row. `$when` names a column that must hold a value for
the row to be added (empty, `0` and no-words such as `N` do not count). Without `$when`, a
row is added when any of its column-based fields has a value; a row of constants only is
always added. On an update the mapped child tables are replaced as a whole.

#### Child entities

When a child table comes from its own file (a link table of students and guardians):

```json
"student_guardians": {
  "mode": "child",
  "file": "student_guardians.csv",
  "doctype": "Hostel Student",
  "table": "guardians",
  "key": "link_id",
  "parent": {"entity": "students", "column": "stud_id"},
  "match_field": "guardian",
  "fields": {"guardian": {"column": "guardian_id", "lookup": "guardians"}, "relation": "relation"}
}
```

Each row is added to (or updates, matched by `match_field`) the parent's table and the
parent is saved.

### 1.4 Repeating a migration (idempotency)

Every migrated row is recorded in **Migration Record Map** (source system + entity +
legacy ID → record, with the SHA-256 of the mapped values). On the next run:

* unchanged rows are skipped (*Unchanged*);
* changed rows update their record (*Updated*), or, for submitted records, are reported;
* new rows are created; a row whose record was deleted in Hostel360 is created again.

Empty cells never clear a value that is already in Hostel360: a blank in a later extract
usually means "not exported". Records edited in Hostel360 after go-live keep those edits
until the legacy row itself changes.

### 1.5 Reconciliation

Computed at the end of every run (also for dry runs, before the rollback):

* **Counts** per entity: source rows, excluded, expected, records in Hostel360 that came
  from the entity, failed. An entity matches when nothing failed and the expected and
  actual numbers are equal.
* **Money**: each `reconcile` total summed from the source column and from the target
  field of the migrated records (cancelled records excluded). Differences of more than half
  a paisa, or source values that are not amounts, are a mismatch.
* **Attachments**: SHA-256 of each legacy file against the bytes stored by Hostel360.

The report is stored on the Migration Run (JSON) and attached as **Excel** (sheets
Summary, Counts, Money, Attachments, Failed Rows), **Markdown** and **JSON** files. The
run is *Completed* only when nothing failed and everything matched; otherwise it is
*Completed with Errors*.

### 1.6 Fees and payments

Migrate invoices with their full amounts and migrate the receipts with allocations to
those invoices (as in the sample): Hostel360 then computes paid and outstanding amounts
itself, and the `outstanding` reconciliation proves they agree with the legacy balances.
Map `"migrated_paid_amount": {"value": 0}` on invoices so no amount is assumed paid.

If the legacy system has no receipt history, map the amount already paid to
`migrated_paid_amount` (or `paid_amount`) on the invoice instead.

Legacy mess bills are best migrated as invoices with `invoice_type` *Mess Bill* and fee
heads *Mess Charges* / *Mess Extras*, as in the sample.

### 1.7 The sample

`samples/` holds a small export of every kind of record (academic years, departments,
programmes, hostels, blocks, floors, rooms, students with photos, guardians and their
links, allotments past and current, fee invoices, mess bills, receipts, leaves and
grievances) in deliberately mixed formats, and `sample_mapping.json` for it:

```
bench --site SITE h360-migrate --source apps/hostel360/hostel360/hostel_migration/samples \
      --mapping apps/hostel360/hostel360/hostel_migration/samples/sample_mapping.json --dry-run
```

Expected result: *Completed*, reconciliation *Matched*; fee invoices ₹1,79,000.00 with
₹72,500.00 outstanding, mess bills ₹12,371.25, receipts ₹1,12,830.50. The sample is for
rehearsals on test sites only.

---

## 2. Exit export (TR 22)

```
bench --site hostel.example.ac.in h360-export --output /srv/exit --encrypt --passphrase-file /root/exit.pass
```

The export folder `hostel360-export-<site>-<timestamp>/` contains:

| Path | Content |
|---|---|
| `data/csv/<DocType>.csv` | Every Hostel360 DocType (discovered from the app's modules), all columns in field order. Child tables have their own file with `parent`, `parenttype`, `parentfield`, `idx`. Single DocTypes are `field,value` pairs. UTF-8 with BOM, so Excel opens Tamil text correctly. |
| `data/xlsx/<DocType>.xlsx` | The same records; each child table as an extra sheet. Text that looks like a formula stays text. |
| `data/core/` | Users (no password data), their roles, the file index, the audit trail (Version) and comments of Hostel360 records. |
| `database/<db>.sql.gz` | Complete MariaDB dump (`mariadb-dump --single-transaction`; Frappe's backup routine if the binary is missing). Credentials are passed through a private option file, never on the command line. Restore with `bench --site SITE restore FILE`. |
| `files/public/`, `files/private/` | Uploaded files (`--no-files` leaves them out). |
| `manifest.json` | Site, apps and versions, DocTypes with row counts, and the size and SHA-256 of every file. |
| `README.txt` | A plain description for the institution's records. |

It is packed into `<name>.tar.gz` (`--no-archive` keeps only the folder). With
`--encrypt` the archive becomes `<name>.tar.gz.h360enc` and the plaintext folder and
archive are deleted. A `.sha256` file next to the archive allows a quick transport check.
The export refuses to write inside the site's public folder.

**Encryption**: AES-256-GCM in 4 MiB chunks, key from the passphrase with scrypt
(n = 2^17, r = 8, p = 1, random 16-byte salt). The header is authenticated with every
chunk and each chunk's nonce carries its number and a last-chunk flag, so any change,
reordering or truncation is detected. Passphrases must be at least 12 characters; keep
the passphrase apart from the archive.

**Verification**: `bench h360-verify-export --path ...` accepts the folder, the archive or
the encrypted archive (asks for the passphrase), checks every file against the manifest,
reports missing, altered and unlisted files, and with `--extract-to DIR` keeps the
decrypted, unpacked copy. It needs no site.

**Export log**: every finished export is recorded in **Exit Export Log** (name, time, whether
files are included, size, the SHA-256 of `manifest.json` and of the archive, and where it
was written). Run the verification *with the site* so the result is recorded there:

```
bench --site hostel.example.ac.in h360-verify-export --path /srv/exit/hostel360-export-...tar.gz.h360enc \
      --passphrase-file /root/exit.pass
```

The log row becomes *Verified* only when every file matches **and** `manifest.json` is the
one written when the export was made (a copy with a rewritten manifest is *Verification
Failed*, even if its files match that manifest). An export made before exports were logged
is adopted into the log the first time it verifies. The deletion after exit (section 5)
relies on this record.

Decrypting without Hostel360 (Python 3 with the `cryptography` package):

```python
import struct, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

HEADER = struct.Struct(">8sBBBBB16s7sI")


def decrypt(src, dst, passphrase):
	with open(src, "rb") as f, open(dst, "wb") as out:
		header = f.read(HEADER.size)
		magic, version, kdf, log2_n, r, p, salt, prefix, _chunk = HEADER.unpack(header)
		assert magic == b"H360ENC1" and version == 1 and kdf == 1
		aead = AESGCM(Scrypt(salt=salt, length=32, n=2**log2_n, r=r, p=p).derive(passphrase.encode()))

		def record():
			raw = f.read(4)
			return f.read(struct.unpack(">I", raw)[0]) if raw else None

		sealed, index = record(), 0
		while sealed is not None:
			following = record()
			nonce = prefix + struct.pack(">I", index) + (b"\x01" if following is None else b"\x00")
			out.write(aead.decrypt(nonce, sealed, header))
			sealed, index = following, index + 1


decrypt(sys.argv[1], sys.argv[2], input("Passphrase: "))
```

---

## 3. Demo and UAT data

```
bench --site uat.example.ac.in h360-demo-data --students 120 --seed 2026
```

Creates a realistic data set for a Tamil Nadu engineering college: Boys Hostel I (BH1,
two blocks), Boys Hostel II (BH2) and the Girls Hostel (GH1), each with three floors of
double and triple rooms and their beds; students from the southern districts with Tamil
names, guardians, programmes and departments; allotments (one in ten students is a day
scholar); this year's fee structures with invoices, six in ten paid in full and some part
paid; two messes with suppliers, the month's provisions received, daily issues and a
published weekly menu; and a few leaves, grievances and bonafide requests.

* The same `--seed` and `--students` always give the same data. Running again creates
  nothing new; a larger `--students` tops the set up.
* Demo register numbers begin with `UAT`; e-mail addresses use `demo.hostel360.invalid`,
  which Hostel360 never sends to. Mobile numbers are random, so the command refuses to
  run while SMS or WhatsApp is live: turn on **Test Mode** in Messaging Settings first.
* No messages are sent while the data is created.

---

## 4. New site setup

```
bench --site hostel.example.ac.in h360-setup \
      --institution-name "Alagappa Chettiar Government College of Engineering and Technology" \
      --short-name ACGCET --admin-email hostel.admin@example.ac.in \
      --portal-url https://hostel.example.ac.in [--admin-name "..."] [--office-email ...]
```

Marks the setup wizard complete with Indian regional settings, applies the Hostel360
security baseline, roles and module defaults, fills Hostel Settings, sets the site's
`host_name` to the portal URL (links in e-mails and background jobs), and creates the
first **Hostel Administrator** (also System Manager unless `--no-system-manager`) with a
random password that is printed once. Two-factor sign-in is enrolled at first login.
Running it again re-applies the settings and leaves an existing administrator's password
alone unless `--reset-password` is given.

---

## 5. Deleting the institution's data after exit (TR 22)

The bid commits HyFib to delete all institutional data permanently once ACGCET confirms in
writing that it has received the exit export, and to issue a deletion certificate. The
application part of that is one command; the stores outside the application are destroyed
with the infrastructure exit runbook (5.5).

### 5.1 Before deleting

1. Make a complete export **with files** and keep its passphrase apart from it:
   `bench --site SITE h360-export --output /srv/exit --encrypt --passphrase-file /root/exit.pass`
2. Verify it on the site (section 2) so the log shows it *Verified*.
3. Hand it over. Record ACGCET's written confirmation of receipt: its reference number,
   date and signatory. The reference is required by the command and printed on the
   certificate.

### 5.2 Dry run (the default)

```
bench --site hostel.example.ac.in h360-purge-institution-data \
      --confirm-site hostel.example.ac.in --acknowledgement-reference "ACGCET/HOSTEL/2027/014"
```

Nothing is deleted. The command checks the safeguards and lists, per DocType and per
store, what would be deleted, which accounts are kept, which records were added or changed
after the export was made (they are not in it), and where the certificate will be written.

Safeguards:

* `--confirm-site` must repeat the site given with `--site`.
* `--acknowledgement-reference` is required (3 to 140 characters, one line).
* The newest **verified** export of the site must be at most **30 days** old and include the
  uploaded files. Otherwise the command refuses; `--force-without-export` overrides only
  this check, and the certificate states that the deletion was forced and why.
* Without `--execute` nothing is deleted; with it, the command asks once more at a terminal
  (`--yes` skips that question in scripts).
* The certificate may not be written inside the site folder or the public assets folder.

### 5.3 Deleting

Add `--execute`. The site is put into maintenance mode and its scheduler paused (they stay
so: the site is being retired), then, in one database transaction:

| Deleted | Details |
|---|---|
| Hostel360 records | Every row of every Hostel360 DocType, child tables and settings included, with their encrypted secrets (`__Auth`). |
| Records pointing at them | File, Version (audit trail), Comment, Communication (and its links), Activity Log, Access Log (including exports of Hostel360 reports), View Log, Notification Log, ToDo, DocShare, Document Follow, Tag Link, Email Queue (and recipients), Error Log (also tracebacks from Hostel360 code), Scheduled Job Log entries mentioning Hostel360, Deleted Document copies, Prepared Report, Data Import (and its log), Submission Queue, Integration Request, Webhook Request Log, Document Share Key, Reminder, Workflow Action, Auto Repeat, Event, Route History of Hostel360 pages, Unhandled Email, search-index rows. |
| People's accounts | Users linked from a student, guardian or staff record, and users holding an institution role (Hostel Administrator, Warden, Deputy Warden, Office Staff, Mess Manager, Mess Staff, Institution Auditor, Student, Parent), with roles, user permissions, sessions, two-factor secrets, saved list settings, defaults, contacts, notification settings, login history and personal-data requests. HyFib Operations accounts and Administrator stay. |
| Files | Uploaded files attached to anything deleted, uploads attached to nothing, and any file in `public/files` or `private/files` that no remaining record uses. Frappe's own theme stylesheets in `public/files/website_theme` stay. |
| Local copies | Everything in `private/backups` (Frappe's own backups) and `private/migration` (legacy extracts), and this server's copy of the exit export (archive, `.sha256` file, folder) as recorded in Exit Export Log (`--keep-export-copy` leaves it). Only paths named `hostel360-export-*` are ever removed from there. |

Afterwards every selection is counted again; anything still present (or a file that could
not be removed) is listed on the certificate and the command exits with `2`.

The Exit Export Log is kept: it holds names, checksums and paths, no student or parent
data, and is stamped with the acknowledgement reference, the time and the certificate. It
goes with the site database in 5.5.

If the database step fails, it is rolled back and nothing is deleted; the site stays in
maintenance mode with the scheduler paused (`bench --site SITE set-maintenance-mode off`,
`bench --site SITE scheduler resume` to reopen it). If the command stops after the database
step, run it again: it finishes the files and the next certificate reports the interrupted
run.

### 5.4 The certificate

Written to `<bench>/hostel360-exit/` (or `--certificate-dir`), readable by the owner only:

* `H360-DEL-<site>-<timestamp>.json`: the authoritative, signed certificate;
* `.md` and, when the PDF renderer is available, `.pdf`: the same content for the file,
  with a line for the authorised signatory.

It records the site, institution, acknowledgement reference, start and end times, operator
and host, the export it relied on (name, dates, **manifest SHA-256**, archive SHA-256), the
row count per Hostel360 DocType and per related record type, accounts, files and bytes,
the check afterwards, what was kept and why, and the stores still to be destroyed.

The signature is HMAC-SHA256 over the canonical JSON (sorted keys, no spaces, UTF-8) with a
key derived from the site's `encryption_key` (`HMAC-SHA256(encryption_key,
"hostel360/deletion-certificate/v1")`); its fingerprint is printed on the certificate. Check
it while the site exists with `bench --site SITE h360-verify-deletion-certificate --path
FILE.json`, and afterwards with `--key-file` holding the encryption key. HyFib keeps that key
in its password vault with the site's other recovery secrets for as long as the
certificate must be verifiable; it decrypts nothing once the backups are gone.

### 5.5 Outside the application (exit runbook)

Listed on every certificate under "Still to be destroyed", each signed off separately:

1. Drop the database and site folder **without** a backup: `bench drop-site SITE --no-backup
   --force`, then delete `<bench>/archived/sites/SITE*` (drop-site moves the folder there).
2. Off-site backups: every restic snapshot of the server and every object **version** and
   unfinished multipart upload in the backup bucket (ap-mumbai-1). The server's own
   credentials cannot delete versions; this needs the tenancy administrator.
3. Block-volume and boot-volume backups in both regions, including cross-region copies and
   the ad-hoc pre-deploy and pre-restore snapshots, which never expire once the server and
   its prune timer are gone.
4. Pre-deployment database dumps (`/srv/hostel360/backups/pre-deploy`) and the restic cache.
5. The export passphrase file and any copy of the export outside this server.
6. The site's Redis data, with the containers.
7. Logs kept under the CERT-In directions (OCI Logging, load balancer, host security and
   site logs) are **not** deleted early: they expire after 180 days, and the certificate
   gives that date.
