#!/usr/bin/env bash
# Rebuild the Hostel360 public demo now, as the nightly reset does at 02:30: restore the site
# from its golden snapshot, clear its cache and enable its scheduler. Visitors who are signed
# in at that moment see an error for the minute or two the restore takes.
#
#   deploy/demo/reset.sh
set -Eeuo pipefail

prog=reset.sh
# shellcheck source=deploy/demo/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
	sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'
	exit 0
fi

require_demo
[[ -n "$(demo_compose ps -q --status running demo-reset 2>/dev/null)" ]] ||
	die "the demo is not running (deploy/demo/up.sh --no-build)"
demo_compose exec -T demo-reset bash /opt/hostel360-demo/demo-reset-loop --now
