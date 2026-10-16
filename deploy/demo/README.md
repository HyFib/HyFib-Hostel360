# HyFib Hostel360 — public demo at https://hostel360.hyfib.com

The production Docker stack (`deploy/compose/compose.yml`: WAF with ModSecurity and the OWASP
CRS, frontend, backend, websocket, workers, scheduler, MariaDB, Redis, ClamAV, PHP-FPM) running
on HyFib's Mac with Docker Desktop, published through a Cloudflare named tunnel so that ACGCET's
hostel staff, students and parents can try Hostel360 before go-live.

It is an **open demo**: anyone with the link can use it, the demo sign-ins are listed at
`https://hostel360.hyfib.com/demo`, every record in it is fictitious, nothing can leave it as a
real SMS, WhatsApp message, e-mail or payment, and it is rebuilt from a clean copy every night.

| File | What it is |
|---|---|
| `up.sh` | Build, start or update the demo; creates and prepares the site; opens the tunnel last |
| `down.sh` | Close the tunnel, then stop the stack (data kept); `--tunnel` closes the tunnel only |
| `reset.sh` | Rebuild the demo data now, as the nightly reset does |
| `compose.sh` | `docker compose` with the demo's project, files and settings (`ps`, `logs`, `exec`, ...) |
| `compose.demo.yml` | Override for the production compose file: loopback ports, memory, tunnel, nightly reset |
| `demo.env.example` | Settings (no secrets); copied to `~/hostel360-demo/demo.env` on the first run |
| `cloudflared/config.yml.template` | Tunnel configuration: the demo host name to the WAF, everything else 404 |
| `mariadb/zz-demo.cnf` | MariaDB sizing for a workstation (1 GB buffer pool, no binary log) |
| `bin/demo-reset-loop` | The nightly reset (command of the `demo-reset` service) |
| `bin/demo-mode-check` | Is the site in demo mode? Used before the tunnel opens and before every reset |
| `lib.sh` | Shared by the scripts |

---

## 1. How it fits together

```
 Visitor (phone, laptop)
     │ HTTPS, Cloudflare's certificate for hostel360.hyfib.com
     ▼
 Cloudflare edge ── named tunnel "hostel360-demo" (outbound from the Mac; no port is opened
     │               on the Mac or the office router)
     ▼
 Docker Desktop on the HyFib Mac, compose project "hostel360-demo"
 ┌────────────────────────────────────────────────────────────────────────────────────────┐
 │ tunnel   cloudflared 2026.9.3, edge network, fixed address 172.30.120.250              │
 │   │ http://waf:8080, visitor's address in X-Forwarded-For                              │
 │   ▼                                                                                    │
 │ waf      ModSecurity + CRS 4 as in production; X-Forwarded-For from the tunnel only    │
 │   ├── frontend (nginx) ── backend (gunicorn 3×4) · websocket                           │
 │   └── php-fpm (/integration/)                                                          │
 │ queue-short · queue-long · scheduler · mariadb · redis-cache · redis-queue · clamav    │
 │ demo-reset (02:30 IST: restore the golden snapshot)  · demo-guard (before the tunnel)  │
 └────────────────────────────────────────────────────────────────────────────────────────┘
   127.0.0.1:18080 / 127.0.0.1:18443 → waf, for checks on the Mac only (never the LAN)
```

Compared with production only what a shared workstation and a tunnel need is different
(`compose.demo.yml`): loopback ports, smaller memory limits, Docker volumes for MariaDB, the
Redis queue and the ClamAV signatures, the tunnel in place of the OCI load balancer, and the
two demo services. The images, the WAF rules and headers, the rate limits, the networks and the
container hardening are the production ones.

### The contract with the application

| Item | Meaning |
|---|---|
| `"hostel360_demo_mode": 1` in the site's `site_config.json` | Demo mode. Set only by `bench h360-demo-site`; nothing in the application's UI can switch it on |
| `bench --site SITE h360-demo-site [--students N] [--seed N]` | One-shot, idempotent preparation of the demo site; takes the golden snapshot at the end |
| `bench --site SITE h360-demo-reset` | Restores the site from the golden snapshot; run every night |
| `sites/SITE/private/demo/` | The golden snapshot (database backup and files), here `~/hostel360-demo/data/sites/hostel360.hyfib.com/private/demo/` |

---

## 2. Safety of an open demo

- **Only a demo is ever published.** `up.sh` opens the tunnel last, after `h360-demo-site` and
  after it has read `hostel360_demo_mode: 1` back from the site. The tunnel depends on the
  one-shot `demo-guard` service, which Compose runs again on every `up` of the tunnel (by
  `up.sh`, `compose.sh up` or by hand) and which keeps the tunnel from starting unless the site
  is in demo mode. (Docker restarting a tunnel that was already running, after a crash or a
  reboot, does not ask again; the site was a demo when it started.) `up.sh --skip-demo-site`
  (for testing the stack) works only together with `--no-tunnel`, and leaves that site's
  scheduler paused.
- **Nothing real goes out.** In demo mode the application does not send SMS, WhatsApp messages
  or e-mail and takes no real payment. On top of that, the demo site is never given payment
  gateway keys, SMS or WhatsApp credentials or an outgoing mail account; keep it that way.
- **Fictitious data only.** Do not put real students, parents or staff into the demo, not even
  to show something; whatever visitors type in is gone after the next nightly reset.
- **The Administrator account is not part of the demo.** Its password is a random one in
  `~/hostel360-demo/config/secrets/site_admin_password` and never appears on `/demo`; use it
  only on the Mac.
- **No LAN exposure.** The WAF is published on `127.0.0.1` only, and `up.sh` refuses any other
  address. The tunnel is an outbound connection; the Mac accepts nothing from outside.
- **Every visitor is counted separately.** The WAF trusts `X-Forwarded-For` only from the
  tunnel's fixed address (`LB_SUBNET_CIDR` = `H360_TUNNEL_IP/32`), so the visitor's own address
  reaches the application, and the per-address limits (20 sign-in codes an hour, the flood
  guard, the WAF's sign-in rate limits) apply per visitor. A forged `X-Forwarded-For` from a
  visitor is ignored: Cloudflare appends the real address, and the WAF takes the last one.
- **Secrets stay off the repository.** The database root and Administrator passwords live in
  `~/hostel360-demo/config/secrets/` (files `0600`, folder `0700`), the tunnel's credentials in
  `~/.cloudflared/<tunnel id>.json`, mounted read-only into the tunnel container and never
  copied. `up.sh` refuses an `H360_DEMO_HOME` or credentials path inside the checkout.
- **HTTPS only.** Turn on *Always Use HTTPS* for `hyfib.com` in Cloudflare (SSL/TLS > Edge
  Certificates). The WAF does not redirect plain HTTP itself (in production the load balancer
  does), and the session cookies are `Secure`, so signing in over plain HTTP would fail.

---

## 3. Prerequisites

- The Mac (Apple silicon) with Docker Desktop and Compose 2.24 or later, and this repository
  checked out. Docker Desktop needs about 4 GB free inside its memory allocation (the demo uses
  1.8 GB idle and grows with use; its limits add up to about 9 GB, a ceiling it does not reach)
  and 15 GB of disk (the image is 3.3 GB, ClamAV's signatures and the database the rest).
- Docker Desktop set to *Start Docker Desktop when you sign in*; every container restarts on its
  own after that. The Mac must not sleep while the demo is meant to be online (System Settings >
  Energy: *Prevent automatic sleeping when the display is off*): asleep, the demo is offline and
  that night's reset is skipped.
- `cloudflared` on the Mac (`brew install cloudflared`), logged in to the Cloudflare account that
  holds `hyfib.com` (`~/.cloudflared/cert.pem`; `cloudflared tunnel login` creates it).
- Free host ports 18080 and 18443 on `127.0.0.1` (others in `demo.env`). Ports 5050, 5080, 5173,
  5432, 5433, 5443, 8000, 8080, 8081, 8090 and 9000 are taken by other projects on this Mac.

### One-time tunnel set-up

Run once, on the Mac, as the user who runs the demo:

```
cloudflared tunnel login                                   # only if ~/.cloudflared/cert.pem is missing
cloudflared tunnel create hostel360-demo                   # prints the tunnel id, writes ~/.cloudflared/<id>.json
cloudflared --config /dev/null tunnel route dns <TUNNEL_ID> hostel360.hyfib.com   # the id, and no default config: see below
```

Then put the id into `~/hostel360-demo/demo.env` (`up.sh` creates that file on its first run; or
copy `demo.env.example` there yourself):

```
TUNNEL_ID=<the id that "cloudflared tunnel create" printed>
```

`cloudflared tunnel list` shows the tunnel, and `hostel360.hyfib.com` becomes a proxied CNAME to
`<id>.cfargotunnel.com`. Pass the tunnel **id** and `--config /dev/null`: with a default `~/.cloudflared/config.yml`
present, cloudflared takes the `tunnel:` named there instead of the one on the command line and
points the record at that other tunnel (with `/dev/null` it logs "Configuration file /dev/null
was empty", which is expected). Check with `dig +short hostel360.hyfib.com CNAME
@<zone name server>` or the Cloudflare dashboard that the record targets `<TUNNEL_ID>.cfargotunnel.com`. If a DNS record for `hostel360` already exists, `route dns` refuses;
remove that record in the Cloudflare dashboard first. The credentials file stays where
`cloudflared` wrote it; `CLOUDFLARED_CREDENTIALS_FILE` in `demo.env` points elsewhere if needed.

---

## 4. Run

```
deploy/demo/up.sh
```

In order, it:

1. checks Docker and Compose, creates `~/hostel360-demo` (`H360_DEMO_HOME`) with `demo.env`, the
   random passwords, the WAF's internal certificate and empty WAF lists;
2. builds `hostel360:<version>-<commit>` for arm64 from this checkout (`deploy/docker/build.sh`;
   unchanged layers come from the build cache), and writes `config/runtime.env` (image tag, paths,
   the tunnel's address as the WAF's only trusted proxy);
3. closes the tunnel if it is open, and starts or updates the stack;
4. creates the site with `hostel360` installed if it does not exist yet (the scheduler stays
   paused), or migrates it when the image changed;
5. runs `bench h360-demo-site` (demo data, demo mode, golden snapshot), reads demo mode back, and
   only then resumes the scheduler;
6. waits until every service is healthy and the readiness check answers through the WAF, and for
   ClamAV (its volume starts with the signatures shipped in its image, which freshclam then
   brings up to date);
7. renders and validates the tunnel configuration, opens the tunnel and waits until it is
   connected, and prints the URL.

The first run takes 5–25 minutes, depending on how much of the image the build cache already
holds (here: about 4 minutes, of which 2 for creating the site); a run with nothing to change
takes seconds. Without `TUNNEL_ID` it stops before the tunnel and the demo is reachable on the
Mac only (`--no-tunnel` does the same on purpose).

### Everyday commands

| Task | Command |
|---|---|
| State of every container | `deploy/demo/compose.sh ps` |
| Logs | `deploy/demo/compose.sh logs -f --tail 100 backend waf tunnel` |
| Last nightly resets | `deploy/demo/compose.sh logs demo-reset` |
| Rebuild the demo data now | `deploy/demo/reset.sh` |
| Take the demo offline, keep it running locally | `deploy/demo/down.sh --tunnel` |
| Back online (no rebuild) | `deploy/demo/up.sh --no-build` |
| Stop everything (data kept) | `deploy/demo/down.sh` |
| Readiness through the WAF, on the Mac | `curl -H 'Host: hostel360.hyfib.com' http://127.0.0.1:18080/api/method/hostel360.api.health.readiness` |
| Tunnel connections | `cloudflared tunnel info hostel360-demo` |
| Bench console | `deploy/demo/compose.sh exec backend bench --site hostel360.hyfib.com console` |
| Memory and CPU | `docker stats --no-stream $(deploy/demo/compose.sh ps -q)` |
| WAF log, blocked requests | `tail -f ~/hostel360-demo/data/logs/waf/access.log` / `error.log` |

### Update to a new commit

```
git pull                      # or check out the commit to show
deploy/demo/up.sh
```

The tunnel closes at the start; the new image is built and the containers recreated; the site is
migrated (`bench migrate`) and `h360-demo-site` runs again; the tunnel reopens once every check
has passed. Older images stay in Docker (`docker images hostel360`); `docker image prune` or
`docker rmi hostel360:<tag>` removes them. A migration cannot be undone: to go back to an older
commit, check it out and start over (below).

### Nightly reset

The `demo-reset` service runs `bench h360-demo-reset` at 02:30 Asia/Kolkata (`DEMO_RESET_AT`),
then clears the site's cache and enables its scheduler. It follows the wall clock: a reset runs
only between 02:30 and 04:00, so a Mac that slept through the night skips that reset (logged)
instead of resetting during the next day's demonstrations. A failed reset is retried every ten
minutes inside that window, three times at most; nothing is reset unless the site is in demo
mode. `logs/h360-demo-reset.last` under `~/hostel360-demo/data` holds the date of the last
successful reset. `deploy/demo/reset.sh` runs one now (visitors signed in at that moment see an
error for the minute or two the restore takes).

### Start over

Removes the demo's database and site; the passwords, the certificate and `demo.env` stay.

```
deploy/demo/down.sh
docker volume rm hostel360-demo_mariadb hostel360-demo_redis-queue
rm -rf ~/hostel360-demo/data/sites/hostel360.hyfib.com ~/hostel360-demo/config/site-image-tag
deploy/demo/up.sh
```

Keep the `hostel360-demo_clamav` volume: it holds the downloaded signatures.

---

## 5. Ports, networks and memory

| What | Value |
|---|---|
| WAF on the Mac | `127.0.0.1:18080` (HTTP), `127.0.0.1:18443` (HTTPS, internal certificate) |
| Networks | edge `172.30.120.0/24` (tunnel at `.250`), internal `172.30.110.0/24`, php `172.30.130.0/24` |
| Volumes | `hostel360-demo_mariadb`, `hostel360-demo_redis-queue`, `hostel360-demo_clamav` |
| Files | `~/hostel360-demo/data/sites` (sites, golden snapshot), `~/hostel360-demo/data/logs` |

| Service | Memory limit (production) |
|---|---|
| mariadb (buffer pool 1 GB) | 1.75 GB (7 GB) |
| clamav | 1.75 GB (same) |
| backend (gunicorn 3 × 4) | 1.5 GB (2 GB) |
| demo-reset | 1 GB (new) |
| queue-long | 640 MB (same) |
| configurator (one-shot) | 512 MB (same) |
| queue-short × 1 | 448 MB (× 2) |
| waf | 384 MB (same) |
| websocket, scheduler, php-fpm | 256 MB each (php-fpm 768 MB) |
| frontend | 192 MB (same) |
| redis-cache, redis-queue (128 MB each) | 192 MB each (576 / 640 MB) |
| tunnel, demo-guard | 128 MB each (new) |

Measured on the HyFib Mac right after a start, idle: 1.8 GB for the whole stack without the
tunnel (ClamAV 980 MB, backend 290 MB, MariaDB 195 MB, WAF 83 MB, scheduler 53 MB, each
worker 46 MB, websocket 44 MB, everything else under 15 MB); all containers on that Docker
Desktop, the other projects included, came to 3.8 GB of its 11.7 GB. MariaDB grows towards
its buffer pool and gunicorn towards about 250 MB per worker as the demo is used.

---

## 6. Troubleshooting

| Symptom | Look at |
|---|---|
| Cloudflare error 1033 | The tunnel is not running: `deploy/demo/compose.sh ps tunnel`, then `deploy/demo/up.sh --no-build` |
| Cloudflare 502 / 504 | The tunnel runs but the WAF does not answer: `deploy/demo/compose.sh ps`, `logs waf` |
| `421 Misdirected Request` | A host name other than `SITE_NAME` reached the WAF; the tunnel's `hostname` and `SITE_NAME` must match (both come from `demo.env`) |
| `403` from the WAF | A CRS rule matched: `~/hostel360-demo/data/logs/waf/error.log` names it |
| `demo-guard` exited 1 | The site is not in demo mode: run `deploy/demo/up.sh` (it runs `h360-demo-site`). That only works on a site that is still a fresh install: once it holds anything else (a user, an upload, records), `h360-demo-site` refuses it and lists what it found; then see Start over |
| `h360-demo-site` refuses the site | It is not a new site (the list says what it holds), or its golden snapshot is missing or damaged: put `sites/SITE/private/demo` back from a backup, or see Start over |
| Tunnel unhealthy | `deploy/demo/compose.sh logs tunnel`; the credentials file in `config/runtime.env` must exist; `cloudflared tunnel info hostel360-demo` |
| ClamAV not ready | freshclam is slow on a poor connection: `deploy/demo/compose.sh logs clamav`, then `up.sh --no-build` again |
| `port … already in use` | Another project took 18080 or 18443: set `WAF_HTTP_PORT` / `WAF_HTTPS_PORT` in `demo.env` |
| `Pool overlaps with other one on this address space` | Another Docker network took one of the subnets: change `H360_*_SUBNET` (and `H360_TUNNEL_IP`) in `demo.env` |
