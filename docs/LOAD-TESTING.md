# Load testing

Tender TR 15 asks for **1,500 concurrent active users without degradation**; the design
target (SOLUTION-DESIGN §10) is about 150 requests a second at peak with 95 % of requests
under 800 ms and fewer than 1 % failed. The evidence is a k6 load test kept in
[`tests/load/`](../tests/load/); its README is the full procedure. This page summarises it.

## 1. What the test does

| Profile | Users | Shape | Purpose |
|---|---|---|---|
| `smoke` | 5 portal + 1 desk | 15 s up, 60 s steady, 10 s down | Check every journey works against any environment |
| `load` | 1,500 portal + 40 desk | 300 users in 2 min, 1,500 by 10 min, 15 min steady, 3 min down (28.5 min) | Acceptance run on staging |

- **Portal users (students)** open the sign-in page, request a code (`send_otp`), sign in
  with the staging site's fixed code (`verify_otp`), fetch the CSRF token and the session,
  accept the privacy notice and confirm details on a first sign-in, then every 8–14 seconds
  open one screen: home 35 % (`get_dashboard`), fees 20 % (`get_fees`), menu 20 %
  (`get_menu`), leave 10 % (`list_leaves`), notifications 10 % (`list_notifications`), full
  reload 5 % (`/hostel` + `get_session`). Each keeps its session, as a phone does.
- **Desk users (office staff)** open list views of students, allotments, invoices, payments,
  leave or grievances through the API, and open a record half of the time.

The run passes only if every k6 threshold holds (exit status 99 otherwise): p95 < 800 ms
and failed < 1 % overall and for sign-in, portal screens and desk lists separately; checks
> 99 %; sign-in failures under 1 % of the users; and, for `load`, at least 140 requests a
second sustained during the steady stage, so a run that never reached the target rate
cannot pass.

## 2. Where and how it is run

The acceptance run must be against **staging built and sized exactly like production**
(VM.Standard.A1.Flex 8 OCPU / 16 GB behind the OCI load balancer and the WAF), never
against production: it needs developer mode for the fixed sign-in code and signs in as real
student records.

Preparation (details in `tests/load/README.md`):

1. At least 1,500 validated, Active students with their own mobile numbers, invoices, menu
   and some history (the anonymised migration rehearsal data).
2. Developer mode and `hostel360_fixed_otp` on the staging site only; Messaging Settings
   **Test Mode** on.
3. The load-test machine's public address exempted exactly like the campus gateway
   (`h360_campus_egress_cidrs` with `h360_environment: staging`), otherwise the WAF rate
   limit, fail2ban and the OTP per-address limit stop the test.
4. A Hostel Office Staff user with an API key for the desk users.
5. A load-test machine near the region with at least 4 cores and 8 GB of memory.

```
export BASE_URL=https://<staging host>
export OTP_CODE=<fixed code> DESK_API_KEY=<key> DESK_API_SECRET=<secret>
tests/load/run.sh smoke      # one minute; every journey must pass first
tests/load/run.sh load       # 28.5 minutes
```

Afterwards: developer mode off, remove `hostel360_fixed_otp`, Test Mode as required, remove
the load-test machine's address from the staging inventory.

## 3. Results and evidence

`run.sh` writes `tests/load/results/<time>-<profile>-<host>.txt` (the k6 summary with each
threshold's pass or fail) and `.json` (every metric). No mobile number, API key or sign-in
code is written. Commit the files of the acceptance run together with the release tag, the
staging shape and the settings used (gunicorn workers and threads from `stack.env`).

| Run | Profile | Environment | Result |
|---|---|---|---|
| 2026-10-15 | smoke | Development bench (development server, no WAF) | All thresholds passed, no failed requests, p95 about 30 ms — a functional check of the journeys, **not capacity evidence** |
| — | load | Staging (production shape) | **To be run before go-live** (open item in the [compliance matrix](COMPLIANCE-MATRIX.md)) |

## 4. If a threshold fails

| Symptom | Look at |
|---|---|
| Sign-in failures or 429s | Load-test machine address not exempt (WAF `rate-exempt.conf`, fail2ban `ignoreip`, Messaging Settings > Trusted Networks); per-number limits when re-running within an hour |
| p95 high on portal screens | `docker stats` on the server; gunicorn workers/threads (`stack.env`); MariaDB slow log; `health.database.latency` |
| Errors under load | Error Log during the run; worker backlog (`health.jobs.backlog`); container restarts (memory limits) |
| Rate below 140 req/s with low latency | The load-test machine is the bottleneck (CPU) or think times were changed |

Capacity levers, in order: gunicorn workers and threads, MariaDB buffer pool and
connections, more OCPUs on the A1 Flex shape (a reboot), a second application VM behind the
load balancer.
