#!/usr/bin/env bash
# docker compose for the Hostel360 public demo, with its project name, compose files and env
# files (and the tunnel, once one is configured), for everything up.sh and down.sh do not do:
#
#   deploy/demo/compose.sh ps
#   deploy/demo/compose.sh logs -f --tail 100 backend waf tunnel
#   deploy/demo/compose.sh logs demo-reset
#   deploy/demo/compose.sh exec backend bench --site hostel360.hyfib.com console
#   deploy/demo/compose.sh restart backend
set -Eeuo pipefail

prog=compose.sh
# shellcheck source=deploy/demo/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [[ "${1:-}" == -h || "${1:-}" == --help || $# -eq 0 ]]; then
	sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'
	exit 0
fi

require_demo
demo_compose_all "$@"
