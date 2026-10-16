# Development

How to run Hostel360 on a workstation, test it, work on the portal, and the conventions the
code follows. Contribution workflow (branches, reviews, commits): [CONTRIBUTING.md](../CONTRIBUTING.md).

## 1. Development bench (`deploy/dev/compose.yml`)

The development stack is four containers: MariaDB 11.8, two Redis instances, and a `bench`
container from `frappe/build:v16` (Python 3.14, Node 24, bench) that sleeps until you open a
shell in it. The repository is mounted at `/workspace/hostel360`; the bench lives in a named
volume, so it survives restarts. Ports are published on `127.0.0.1` only: 8000 (web),
9000 (socket.io) and 5173 (portal dev server).

```bash
docker compose -f deploy/dev/compose.yml up -d
docker exec -it h360-dev-bench-1 bash          # always "bash", not "bash -l"
```

### First-time setup (inside the container)

```bash
cd /home/frappe
bench init --ignore-exist --skip-redis-config-generation --frappe-branch version-16 --dev frappe-bench
cd frappe-bench
bench set-config -g db_host mariadb
bench set-config -g redis_cache redis://redis-cache:6379
bench set-config -g redis_queue redis://redis-queue:6379
bench set-config -g redis_socketio redis://redis-queue:6379
bench get-app /workspace/hostel360 --soft-link       # apps/hostel360 -> your checkout
bench new-site h360.localhost --db-root-password devroot --admin-password admin --install-app hostel360
bench --site h360.localhost set-config -p developer_mode 1
bench --site h360.localhost set-config clamd_host ""  # no ClamAV in the dev stack: scanning off
bench use h360.localhost
```

`DEV_DB_ROOT_PASSWORD` (default `devroot`) sets the MariaDB root password. The production
image pins Frappe **v16.52.0** (`deploy/docker/build.sh`); use `--frappe-branch v16.52.0`
instead of `version-16` to match it exactly.

### Every day

```bash
bench start                                   # web :8000, socket.io, scheduler, worker
bench --site h360.localhost migrate           # after any DocType JSON change or pull
bench build --app hostel360                   # desk bundles and the portal build
bench --site h360.localhost console           # IPython with frappe connected
bench --site h360.localhost h360-demo-data    # realistic data set (deterministic, re-runnable)
```

- Desk: http://localhost:8000/desk (Administrator / admin). Two-factor authentication is on
  for every role; enrol the authenticator app at first sign-in, or for local work only switch
  it off in System Settings on your own dev site.
- Portal: http://localhost:8000/hostel. Students and parents sign in with OTP. With
  `developer_mode` on, `bench --site h360.localhost set-config hostel360_fixed_otp 123456`
  makes every code that value (ignored whenever developer mode is off). Turn on **Test Mode**
  in Messaging Settings so nothing is sent. Only **validated** students (Hostel Student >
  Validate) can sign in.
- Payments: Payment Settings with the gateway's test keys and **Test Mode**; add yourself as
  a test payer (Test Payers) to pay a demo student's dues.

## 2. Tests

### Server (Python)

Run against a dedicated test site so test data never mixes with your work:

```bash
bench new-site test.localhost --db-root-password devroot --admin-password admin --install-app hostel360
bench --site test.localhost set-config allow_tests true
bench --site test.localhost set-config clamd_host ""

bench --site test.localhost run-tests --app hostel360                                  # everything
bench --site test.localhost run-tests --module hostel360.hostel_fees.tests.test_payu   # one module
bench --site test.localhost run-tests --doctype "Room Allotment"                       # one DocType
bench --site test.localhost run-tests --app hostel360 --coverage                       # sites/coverage.xml
```

PDF tests need `wkhtmltopdf` 0.12.6 with patched Qt (present in `frappe/build`).

Rules for tests:

- Class `Test<Name>(IntegrationTestCase)` from `frappe.tests`, file `test_<name>.py` beside the
  code, or in a module's `tests/` package.
- Build your own fixtures with the factories in `<module>/testing.py` (unique values from
  `frappe.generate_hash`); never depend on another test's records or on demo data.
- Cover the business rule: the happy path, each validation, permissions (who may and may not),
  idempotency and concurrency where they matter (payments, allotments, stock).
- External services are faked: gateways and messaging providers are patched, the clock is
  patched with `frappe.utils` helpers, ClamAV is off or mocked.

### Portal (TypeScript)

```bash
cd /workspace/hostel360/frontend
yarn install --frozen-lockfile
yarn typecheck      # vue-tsc over the app, the tests and the build config
yarn test --run     # Vitest + @vue/test-utils (jsdom)
yarn build          # writes hostel360/public/frontend and hostel360/www/hostel.html
```

`en.json` and `ta.json` must have identical keys (a test enforces it).

### Load and deployment checks

- k6 load test: [LOAD-TESTING.md](LOAD-TESTING.md) (`tests/load/run.sh smoke` runs against
  the dev bench).
- Edge and server configuration: `deploy/tests/*/run.sh` and the commands in
  `deploy/README.md` §5 (ShellCheck, Terraform validate, Ansible lint, Compose config).

### Continuous integration

`.github/workflows/ci.yml` runs on every pull request and on `main`:

| Job | What |
|---|---|
| Lint | `ruff check` and `ruff format --check` (ruff pinned in the workflow) |
| Portal | `yarn install --frozen-lockfile`, `yarn typecheck`, `yarn test --run`, `yarn build`, on Node 24 |
| Server | MariaDB 11.8 and Redis services, `bench init` with Frappe v16.52.0 on Python 3.14, `bench get-app` from the checkout, a new site with `hostel360` installed, `run-tests --app hostel360 --coverage`; JUnit and coverage reports are attached to the run |

`.github/workflows/security.yml` adds CodeQL, gitleaks, osv-scanner, pip-audit, yarn audit,
Trivy, Checkov and ShellCheck. Protect `main` (repository settings, branch protection) so a
pull request merges only when the CI jobs and the blocking security jobs are green.

## 3. Working on the portal

The portal is a Vue 3 + TypeScript SPA in `frontend/`, served by Frappe at `/hostel`. Its
own README (`frontend/README.md`) explains the build output, the CSP and the payment flows.

Dev server with hot reload, proxied to the bench:

```bash
cd /workspace/hostel360/frontend
yarn dev --host 0.0.0.0 --port 5173
```

Open http://localhost:5173/hostel/ on the workstation. `/api`, `/assets/frappe`, `/files`
and `/private` are proxied to `http://localhost:8000` (`H360_BACKEND` to point elsewhere).
Sign in through the portal's own OTP screen.

Portal rules:

- Talk to the server only through `src/api/portal.ts` and `src/api/auth.ts` (one function
  per endpoint in [API.md](API.md)); never call `/api/resource`.
- No inline scripts or styles that the CSP would block; no third-party scripts except
  Razorpay Checkout, loaded only when a payment starts.
- Every user-visible string goes through `i18n` with English and Tamil text; dates and money
  through `utils/format.ts`; times in hostel time (Asia/Kolkata).
- Mobile first: works on a small Android phone on a slow connection; accessible (WCAG 2.1 AA:
  labels, focus order, contrast, touch targets).
- Dependencies: exact versions in `package.json`, committed `yarn.lock`, and every release at
  least 14 days old (`yarn deps:age`).

## 4. Code conventions

### Layout

```
hostel360/<module>/doctype/<snake_name>/{__init__.py, <snake_name>.json, <snake_name>.py, <snake_name>.js, test_<snake_name>.py}
hostel360/<module>/services.py      business functions used by the portal, desk and other modules
hostel360/<module>/setup.py         idempotent install_defaults(), run after install and every migrate
hostel360/<module>/report/<name>/   Script Reports (execute(filters) -> columns, data[, ...])
hostel360/<module>/page/<name>/     desk pages
hostel360/<module>/testing.py       test factories
```

`docs/SOLUTION-DESIGN.md` is the contract: DocType and field names, statuses, signatures,
endpoints. Add to it; never rename or drop a contract item without changing the document.

### Python

- Formatting and lint: ruff (`pyproject.toml`): tabs, double quotes, line length 110, rule
  sets F, E, W, I, UP, B, RUF and S. `pre-commit install` runs it on every commit.
- `import frappe`, `from frappe import _`; every user-facing string through `_()` (JS:
  `__()`); system text that is stored (timeline entries) is stored in English and translated
  when read.
- Role names from `hostel360.setup.roles`, never as literals.
- Dates with `frappe.utils` (`getdate`, `nowdate`, `now_datetime`, `add_days`); the site
  time zone is Asia/Kolkata.
- SQL through `frappe.qb` or `frappe.db.sql(query, values)` with `%(name)s` placeholders;
  never interpolate input. Lock rows (`for_update=True`) where two people can race: beds,
  payment completion, stock.
- Whitelisted methods: `methods=["POST"]` for anything that changes data; typed parameters;
  explicit permission checks (`doc.check_permission`, roles, `assert_can_access_student`).
  Guest endpoints only where the design says so, always rate limited.
- Background work with `frappe.enqueue(..., queue="short"|"long", job_id=..., deduplicate=True,
  enqueue_after_commit=True)`.
- Notifications through `hostel_communication.dispatch.notify`; problems for HyFib through
  `hostel_operations.alerts.raise_alert`. Import other modules lazily inside functions so a
  module never fails to import because another is not installed.
- Logging through `frappe.logger("hostel360")` or `frappe.log_error(title=..., message=...)`;
  never log OTPs, secrets or full mobile numbers (`utils.validation.mask_mobile`). No `print`.
- Comments explain why, not what.

### DocType JSON

`track_changes: 1`, `sort_field: "creation"`, `sort_order: "DESC"`, `engine: "InnoDB"`,
`field_order` matching `fields`, permissions per the design matrix, `read_only` and
`search_index` where they belong, `naming_rule` matching `autoname`. Submittable DocTypes have
`amended_from`. Bump `modified` whenever the JSON changes so migrate re-syncs it.

### Desk JavaScript

`frappe.ui.form.on("DocType", {...})`, `frm.add_custom_button`, `frappe.call` with `freeze`;
list indicators in `<snake>_list.js`; tabs for indentation; no business rules that the server
does not also enforce.

## 5. Useful references

- Frappe source in the dev container: `/home/frappe/frappe-bench/apps/frappe/frappe`.
- Example of a DocType JSON: `apps/frappe/frappe/desk/doctype/note/note.json`.
- The legacy migration tools and their mapping format: `hostel360/hostel_migration/README.md`.
