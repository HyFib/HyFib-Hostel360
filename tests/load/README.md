# Hostel360 load test (k6)

Evidence for the capacity requirement (tender TR 15, design section 10): **1,500 concurrent
users, about 150 requests a second at peak, 95 % of requests under 800 ms, fewer than 1 %
failed**. One script, two profiles:

| Profile | Users | Shape | Use |
|---|---|---|---|
| `smoke` | 5 portal + 1 desk | 15 s up, 60 s steady, 10 s down | Check the journeys work against any environment, including a developer's laptop |
| `load` | 1,500 portal + 40 desk | 300 users in 2 min, 1,500 by 10 min, 15 min steady, 3 min down (28.5 min) | Acceptance run on staging |

What each user does:

- **Portal (students)**: opens `/hostel/login`, requests a sign-in code (`send_otp`), signs in
  with the staging site's fixed code (`verify_otp`), fetches the CSRF token and the session,
  and on a first sign-in accepts the privacy notice and confirms the details, exactly as the
  SPA does. Then, every 8-14 s, one screen: home 35 % (`get_dashboard`), fees 20 %
  (`get_fees`), menu 20 % (`get_menu`), leave 10 % (`list_leaves`), notifications 10 %
  (`list_notifications`), full reload 5 % (`/hostel` page + `get_session`). Each user signs
  in once and keeps the session, like a phone does.
- **Desk (office staff)**: with a Hostel Office Staff user's API key, opens a list view
  (`frappe.desk.reportview.get` + `get_count`) of students, allotments, invoices, payments,
  leave or grievances, and opens the first record half of the time (`getdoc`).

Pass/fail is decided by k6 thresholds (exit status 99 when one fails): p95 < 800 ms and
failed < 1 % overall and separately for sign-in, portal screens and desk lists; checks
> 99 %; sign-in failures under 1 % of the users; and, for `load`, at least 140 requests a
second sustained during the steady stage (`http_reqs{phase:peak}`), so a run that never
reached the target rate cannot pass.

## Running against staging

Staging must be the production build on the production shape (VM.Standard.A1.Flex,
8 OCPU / 16 GB) behind the OCI load balancer and the WAF, so the result means something for
production. Never run this against production: it needs developer mode for the fixed
sign-in code, and it signs in as real student records.

1. **Data.** At least as many portal-eligible students as portal users (1,500 for `load`):
   validated, status Active, each with their own mobile number - the anonymised migration
   rehearsal data is the natural source - with their fee invoices, the mess menu and some
   leave and grievance history, so the screens return realistic amounts of data.
2. **Fixed sign-in code (staging only).** The code is honoured only with developer mode on,
   so a key left in a production site_config has no effect:

   ```
   bench --site <staging-site> set-config -p developer_mode 1
   bench --site <staging-site> set-config hostel360_fixed_otp <6 digits>
   ```

   In Messaging Settings switch on **Test Mode**, so no SMS or WhatsApp message is sent.
   Developer mode slightly slows template rendering (the `/hostel` page), so the result errs
   on the safe side.
3. **Source addresses.** Every request comes from the load generator's address, which the
   edge treats as one client: the WAF's sign-in rate limit (120 a minute per address),
   fail2ban, and the OTP limit of 20 codes an hour per address would all stop the test.
   Exempt the generator exactly as production exempts the campus gateway - in the staging
   inventory set `h360_environment: staging` and put the generator's public egress address(es)
   in `h360_campus_egress_cidrs`, then run the playbook (`--tags app,fail2ban`); the app role
   also writes them into Messaging Settings > Trusted Networks. Running k6 from several
   machines (distinct addresses) spreads the load but still needs each address exempted,
   since 1,500 sign-ins exceed the per-address limits many times over.
4. **Limits that remain.** Each number may request 5 codes an hour (60 s apart), and the
   sign-in endpoints accept 3,000 calls an hour per address: one full `load` run per hour from
   one address, and at most five runs an hour with the same students.
5. **Desk user.** Create a desk user with the Hostel Office Staff role (no hostel
   assignment needed) and generate its API key and secret (User > Settings > API Access >
   Generate Keys). Token authentication needs no interactive two-factor sign-in.
6. **Run** from a machine with Docker (or k6 on the PATH), close to the region (Hyderabad or
   Mumbai), with at least 4 CPU cores and 8 GB of memory for 1,540 virtual users:

   ```
   export BASE_URL=https://<staging host name>
   export OTP_CODE=<the fixed code>  DESK_API_KEY=<key>  DESK_API_SECRET=<secret>
   tests/load/run.sh smoke        # a minute; check every journey passes first
   tests/load/run.sh load         # 28.5 minutes
   ```

   Student numbers are read with the desk API key (each virtual user reads its own) or from
   `MOBILES_FILE` (one 10-digit number per line). They are never written to the results.
7. **Afterwards** turn developer mode off
   (`bench --site <staging-site> set-config -p developer_mode 0`) and delete
   `hostel360_fixed_otp` from the site's `site_config.json` (bench has no command to remove a
   key; the code is ignored anyway once developer mode is off), switch Test Mode off if
   staging should send messages again, and remove the generator's address from the staging
   inventory.
8. **Keep the evidence.** `run.sh` writes `results/<time>-<profile>-<host>.txt` (the k6
   summary as printed) and `.json` (all metrics). Commit the files of the acceptance run
   together with the release tag, the staging shape and the settings used (gunicorn workers
   and threads from `stack.env`).

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `BASE_URL` | (required) | Site address, `https://...` |
| `OTP_CODE` | (required for portal users) | The staging site's fixed sign-in code |
| `DESK_API_KEY`, `DESK_API_SECRET` | | Hostel Office Staff API key; required for desk users and to read student numbers |
| `MOBILES_FILE` | | Numbers to sign in with, one per line, instead of reading them from the site |
| `SITE_HOST` | | Host header, when `BASE_URL` addresses the server by IP or another name |
| `PORTAL_VUS`, `DESK_VUS` | per profile | Concurrent portal and desk users |
| `RAMP_UP`, `HOLD`, `RAMP_DOWN` | per profile | Stage lengths (`90s`, `15m`, `1h`) |
| `THINK_MIN`, `THINK_MAX` | 8 / 14 (`load`) | Seconds between a user's screens |
| `MIN_PEAK_RATE` | 140 (`load`) | Requests a second the steady stage must sustain; 0 turns the check off |
| `RESULT_LABEL` | from `BASE_URL` | Part of the results file name |
| `K6_IMAGE`, `K6_NETWORK`, `K6_DOCKER` | `grafana/k6:2.3.0`, `bridge`, 0 | Container image and network when k6 is not installed; `K6_DOCKER=1` forces the container |

## Smoke run on a development bench

The development bench (`deploy/dev/compose.yml`) serves the default site on port 8000 inside
the bench container; with `developer_mode` and `hostel360_fixed_otp` in its site config:

```
BASE_URL=http://localhost:8000 K6_NETWORK=container:h360-dev-bench-1 \
OTP_CODE=<fixed code> DESK_API_KEY=<key> DESK_API_SECRET=<secret> \
RESULT_LABEL=dev-bench tests/load/run.sh smoke
```

A development bench says nothing about capacity (one machine, the development web server,
no WAF); it shows that the journeys and checks work before the staging run.
