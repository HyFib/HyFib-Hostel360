#!/usr/bin/env bash
# Stop the Hostel360 public demo.
#
#   deploy/demo/down.sh             close the tunnel, then stop and remove the containers
#   deploy/demo/down.sh --tunnel    close the tunnel only; the stack keeps running locally
#
# The public URL goes offline first. Data is kept: the sites, the golden snapshot and the
# logs under ~/hostel360-demo/data, and the Docker volumes for MariaDB, the Redis queue and
# ClamAV. "deploy/demo/up.sh --no-build" brings the same demo back. A nightly reset that is
# running is allowed to finish first (up to ten minutes).
set -Eeuo pipefail

prog=down.sh
# shellcheck source=deploy/demo/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

tunnel_only=0
case "${1:-}" in
	"") ;;
	--tunnel) tunnel_only=1 ;;
	-h | --help)
		sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'
		exit 0
		;;
	*) die "unknown option: $1 (see --help)" ;;
esac

command -v docker >/dev/null || die "docker is not installed"
docker info >/dev/null 2>&1 || die "Docker is not running"
require_demo

if [[ -n "$(demo_compose --profile tunnel ps -q tunnel 2>/dev/null)" ]]; then
	say "closing the tunnel"
	demo_compose --profile tunnel stop tunnel >/dev/null
fi
if [[ "${tunnel_only}" == 1 ]]; then
	note "the stack keeps running on this Mac; open the tunnel again with deploy/demo/up.sh --no-build"
	exit 0
fi

say "stopping the stack"
demo_compose --profile tunnel down --remove-orphans

note "data kept in ${data_dir} and the Docker volumes ${project}_mariadb, ${project}_redis-queue, ${project}_clamav"
note "start again with: deploy/demo/up.sh --no-build"
