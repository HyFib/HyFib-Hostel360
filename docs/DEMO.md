# Hostel360 demonstration site

The demonstration site is a complete, working Hostel360 for the ACGCET hostels that anyone
with the link can open and try: <https://hostel360.hyfib.com>. Every record in it is
invented, nothing can leave it as a real SMS, WhatsApp message, e-mail, alert or payment, and
it is put back to a clean copy every night.

This document covers the application side: what demonstration mode changes, how a site is
prepared and reset, and what is guaranteed. Running the stack on the HyFib Mac behind the
Cloudflare tunnel is described in [`deploy/demo/README.md`](../deploy/demo/README.md).

---

## 1. What a visitor sees

| Where | What |
|---|---|
| Every page | A strip, in English and Tamil, that cannot be dismissed: *Demonstration site — all data is fictitious*, with a link to `/demo`. On the portal it sits at the top, on the desk at the bottom of the window, on `/verify-certificate` above the card. |
| `/` and `/demo` | What the demonstration shows, the sign-ins (students and parents with their mobile numbers and the demonstration code, every staff role with its login and the shared password) and what is safe about the site. |
| `/hostel` sign-in | A *Demo sign-in* panel listing the demo students and parents. One tap fills in the number and asks for the code; the code to enter is shown on the page. |
| Fees (portal) and payment settings, transactions and invoices (desk) | *Online payment is switched off on this demonstration site; the office records payments.* |

### Demo accounts

Staff sign in at `/login` with the shared password shown on `/demo` (no second factor):

| Login | Role | Looks after |
|---|---|---|
| `admin@demo.invalid` | Hostel Administrator | All hostels and settings |
| `warden@demo.invalid` | Hostel Warden | Chief warden, all hostels |
| `deputy.warden@demo.invalid` | Deputy Warden | Boys Hostel II only (shows hostel scoping) |
| `office@demo.invalid` | Hostel Office Staff | Registrations and fees, all hostels |
| `mess@demo.invalid` | Mess Manager | Boys Mess and Girls Mess |
| `security@demo.invalid` | Hostel Security | Gate pass, all hostels |
| `auditor@demo.invalid` | Institution Auditor | Read-only reports, dashboards, monitoring |
| `operations@demo.invalid` | HyFib Operations | Alerts, health checks, support tickets |

There is no System Manager among them; the Administrator account is not part of the demo and
its password is never shown.

Students and parents sign in at `/hostel` with their mobile number and the demonstration code
(`246810` unless configured otherwise):

| Number | Who | What to try |
|---|---|---|
| 90000 00001 | Student, Boys Hostel I | Has fees due: dues, receipts, the fee structure |
| 90000 00002 | Student, Boys Hostel II | Has a leave from today waiting for the parent |
| 90000 00003 | Student, Girls Hostel | Menu, mess bills, grievances, certificates |
| 90000 00011 | Parent of the Boys Hostel II student | Approves the leave; the Deputy Warden then approves it and Security checks the student out |
| 90000 00012 | Parent of two wards (brother and sister) | Switches between wards |

The names are chosen from the data set when the site is prepared; `/demo` shows the current ones.

### The data

`h360-demo-site` builds the data set with the generator behind `bench h360-demo-data`
(`hostel360/hostel_migration/demo_data.py`): three hostels (Boys Hostel I, Boys Hostel II, Girls
Hostel) with blocks, floors, rooms and beds; 240 students by default with their guardians and
allotments (every tenth student a day scholar); this year's fee structures with paid, part-paid
and overdue invoices and receipts; two messes with suppliers, last month's and this month's stock
receipts, daily issues and expenses, menus for last, this and next week, approved and pending mess
reductions and last month's published mess bills (whose invoices are this month's dues); leaves in
every state, several of them today; grievances in every state, one escalated; bonafide requests
pending, approved and issued; monthly feedback; and on the operations side support tickets, three
weeks of resolved alerts, monitoring samples (the uptime probe every minute), backup records and
last month's Monthly Performance Report. Names, addresses and numbers are invented; e-mail
addresses are on the reserved `.invalid` domain.

---

## 2. Preparing a site

```
bench --site hostel360.hyfib.com h360-demo-site [--students 240] [--seed 2026] [--portal-url URL] [--keep-password]
```

On a new site with Hostel360 installed, in this order:

1. switches demonstration mode on (`hostel360_demo_mode: 1`, `mute_emails: 1`) before anything
   else, so nothing can be sent from that moment;
2. runs the ordinary site setup (`h360-setup`): ACGCET as the institution, the portal address
   (`https://<site>` unless `--portal-url` says otherwise), the first Hostel Administrator (without
   System Manager);
3. makes the site safe to open: two-factor sign-in off site-wide, no forced password expiry,
   messaging channels on but in test mode, online payment off, operations alerting with fictitious
   contacts and recipients only, `/demo` as the home page;
4. creates the data set (section 1), then the staff accounts and their Hostel Staff records with
   hostel assignments, the portal accounts of the demo students and parents (privacy notice
   accepted, details confirmed), and gives the staff some work (assigned grievances, the messes,
   the hostels' warden, the support tickets);
5. writes a new shared password (rotated on every run unless `--keep-password`), the demo users
   and accounts into `site_config.json`, signs everybody out and takes the golden snapshot;
6. prints the sign-ins.

It refuses a site that holds students whose register numbers do not start with `UAT`, or staff
records outside `@demo.invalid`: an open demonstration must never be made from real data. Running
it again is safe: it first restores the last snapshot (so nothing a visitor changed ends up in the
new one), tops up whatever is missing, and takes a new snapshot. Exit code 2 means some demo
records could not be created; they are listed.

## 3. The nightly reset

```
bench --site hostel360.hyfib.com h360-demo-reset
```

1. Refuses unless the site is in demonstration mode and has a snapshot.
2. Puts the site into maintenance mode and waits 65 seconds, so every web worker and the
   scheduler have seen it (they read `site_config.json` at most a minute late).
3. Loads the snapshot's database with the site's own database user (every table is dropped and
   created again) and replaces the uploaded files with the snapshot's.
4. Writes the demo keys back into `site_config.json` from the snapshot.
5. If the code changed since the snapshot (app versions, DocType definitions, patch lists), runs
   `bench migrate` and, once the reset has finished, takes a new snapshot.
6. Moves every date and time forward by the time since the snapshot: date-times by the exact
   interval, dates by calendar days, in all Hostel360 records and their comments, versions,
   notifications and to-dos. What was "this afternoon" or "due next week" stays so.
7. Adds what belongs to the calendar rather than to "now": this week's and next week's menus,
   last month's mess bills (computed and published like the mess office does) and Monthly
   Performance Report, this month's feedback.
8. Signs everybody out and forgets sign-in counters, locks and provider breakers, then ends
   maintenance mode.

It takes a little over a minute for the default data set. If it fails after the restore has
started, the site stays in maintenance mode (a half-restored site is never served) and the error
is in the bench logs; run the reset again. In the demo stack the `demo-reset` service runs it at
02:30 IST.

A menu belongs to a week and a mess bill to a month, so those are never moved; the academic year
is not moved either. Re-run `h360-demo-site` at the start of a new academic year.

## 4. Site configuration

| Key | Written by | Meaning |
|---|---|---|
| `hostel360_demo_mode` | `h360-demo-site` | `1`: the site is a demonstration site. Nothing in the desk or the portal can set or clear it. |
| `hostel360_demo_password` | `h360-demo-site` | The shared staff password shown on `/demo`; a new one on every run unless `--keep-password` |
| `hostel360_demo_otp` | `h360-demo-site` (kept if set) | The portal demonstration code, 4 to 8 digits; default `246810` |
| `hostel360_demo_users` | `h360-demo-site` | The accounts locked against changes (section 5) |
| `hostel360_demo_accounts` | `h360-demo-site` | What `/demo` and the sign-in helper list |
| `mute_emails` | `h360-demo-site` | Frappe's own switch: no e-mail leaves the site |

The golden snapshot is in `sites/<site>/private/demo/` (folder `0700`, files `0600`):
`database.sql.gz`, `public-files.tar.gz`, `private-files.tar.gz` and `manifest.json` (when it was
taken, the app versions and schema fingerprints, the demo keys, the sizes and SHA-256 of the
files). It is outside `private/files`, so it is never served and never part of itself.

## 5. Safety guarantees

Every safeguard reads `site_config.json`, so no setting a signed-in visitor can change undoes it.

- **No message leaves.** The SMS (MSG91 and generic HTTP), WhatsApp and e-mail providers return
  before anything else, whatever Messaging Settings say: every message, sign-in codes included,
  is logged in Message Log as *Skipped* with the reason *Demonstration site*. `mute_emails` stops
  Frappe's own e-mail (password resets, the Monthly Performance Report, notifications) as well.
- **No alert leaves.** Operations alert deliveries (e-mail, SMS, WhatsApp, Slack, Teams, Google
  Chat and generic webhooks, and *Send Test Notification*) are not sent; the alert's timeline says
  so. The dead-man heartbeat is never called and OCI subscription confirmations are not made.
- **No payment gateway is called.** No gateway adapter is handed out at all: online payment is
  refused with the message above, reconciliation and the desk checks find no gateway to ask, and
  webhooks are answered *not configured*.
- **Sign-in codes.** The demonstration code works only in demonstration mode and only for numbers
  that belong to records on the site; for any other number there is nothing to guess against.
  Every limit stays on: resend cooldown, codes per number and per network, wrong attempts per
  code, the one-hour lock after ten wrong codes. Because the code is the same for everyone, it may
  be entered without asking for a fresh one, so visitors trying the same account at the same time
  do not wait on each other.
- **The demo accounts cannot be taken over.** For the users in `hostel360_demo_users` the password,
  login ID, mobile number, enabled state, user type, roles and role profiles, module profile,
  allowed modules, social logins, sign-in hours and IP restrictions, simultaneous sessions and
  API keys cannot be changed, and the accounts cannot be deleted or renamed; their Hostel Staff
  records keep their designation, login, e-mail, active state and hostel assignments and cannot be
  deleted. A desk user who tries is told why. Only the demo command changes them.
- **Two-factor sign-in is off** on a demonstration site, because the accounts are shared. The live
  system requires it for every staff account; nothing about that changes on production sites.
- **Fictitious data only.** The command refuses a site that holds real records, every contact in
  the operations settings is fictitious, and visitors are asked not to enter real details. Whatever
  they enter is gone after the next reset.

On a production site the keys are absent and none of this code changes anything: providers send,
gateways are called, the fixed development code needs developer mode, `/demo` is a 404 and no
banner is shown.

## 6. What visitors can still do

The demonstration is meant to be used, so the demo accounts can do everything their role can,
including changing settings (for example switching the student portal off) and records. That lasts
until the next reset; run `h360-demo-reset` to put the site back at any time. Five wrong passwords
lock a staff account for ten minutes, and ten wrong codes lock a portal number for an hour, as in
production: the demo office account can lift a portal lock (*Unlock Sign-in* on the student or
guardian record), and the nightly reset clears every lock.

## 7. Tests

`hostel360/demo/test_demo.py` covers both sides: with the mode off, the fixed code is refused, the
banner and `/demo` are absent, providers and gateways behave as before and listed accounts are not
locked; with the mode on, the code works only for registered numbers and keeps the limits, every
provider, alert channel, the heartbeat, the gateways and the webhooks are muted, the demo accounts
and their staff records are locked, and `/demo`, the boot data and the certificate banner carry what
they should. `frontend/tests/demo.spec.ts` covers the portal banner and the sign-in helper.
