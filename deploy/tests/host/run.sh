#!/usr/bin/env bash
# Host script checks: h360-ingest (message_id, which answers are retried, spool limits and
# the probe-sample exemption, lock contention), h360-uptime-probe (one alert per outage),
# the worker queue guard of the image entrypoint, the Uptime Kuma ingest relay, and
# h360-decommission against a stand-in server (decommission.sh).
#
#   deploy/tests/host/run.sh        Docker with the image built from deploy/tests/Containerfile
#
# The scripts run as root in a throwaway container against a local stand-in for the ingest
# API (ingest_stub.py), installed where the server has them; nothing on this machine changes.
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy="$(cd "${here}/../.." && pwd)"
image="${H360_CHECKS_IMAGE:-hostel360-deploy-checks:1}"

status=0
docker run --rm -v "${deploy}:/work/deploy:ro" "${image}" bash /work/deploy/tests/host/checks.sh || status=1
# h360-decommission deletes the server's files: a container of its own.
docker run --rm -v "${deploy}:/work/deploy:ro" "${image}" bash /work/deploy/tests/host/decommission.sh || status=1
exit "${status}"
