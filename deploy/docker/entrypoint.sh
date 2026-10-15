#!/bin/bash
# Entrypoint for every Hostel360 container role.
#
#   backend | websocket | worker <queues> | scheduler | frontend | configure | migrate
#
# Anything else is executed as given (bench commands, a shell for support work).
#
# Before starting a role it makes the sites volume consistent with this image: the built
# assets are linked from the image layer and apps.txt lists the apps the image contains.
# Several containers start at once against the same volume, so every change is made with
# an atomic rename.
set -Eeuo pipefail

umask 0002

BENCH_DIR=/home/frappe/frappe-bench
SITES_DIR="${BENCH_DIR}/sites"
BAKED_ASSETS="${BENCH_DIR}/assets"
ROLE_FILE=/tmp/h360-role

log() {
	printf '%s h360-entrypoint: %s\n' "$(date -Iseconds)" "$*" >&2
}

replace_atomically() {
	# replace_atomically <tmp> <target>: rename over target (works for files and links)
	mv -Tf -- "$1" "$2"
}

link_assets() {
	local target="${SITES_DIR}/assets"
	if [[ -L "${target}" && "$(readlink -- "${target}")" == "${BAKED_ASSETS}" ]]; then
		return 0
	fi
	if [[ -d "${target}" && ! -L "${target}" ]]; then
		# A real directory here was left by an older layout; keep it out of the way.
		local stale
		stale="${SITES_DIR}/.assets-stale-$(date +%Y%m%d%H%M%S)-$$"
		mv -- "${target}" "${stale}" 2>/dev/null || true
		rm -rf -- "${stale}" || true
	fi
	local tmp="${SITES_DIR}/.assets-link-$$"
	ln -sfn -- "${BAKED_ASSETS}" "${tmp}"
	replace_atomically "${tmp}" "${target}"
}

write_apps_txt() {
	local tmp
	tmp="$(mktemp "${SITES_DIR}/.apps.txt.XXXXXX")"
	# Frappe must come first; the remaining apps in a stable order.
	{
		echo frappe
		find "${BENCH_DIR}/apps" -mindepth 1 -maxdepth 1 -type d ! -name frappe -printf '%f\n' | sort
	} >"${tmp}"
	chmod 0644 "${tmp}"
	if cmp -s "${tmp}" "${SITES_DIR}/apps.txt"; then
		rm -f -- "${tmp}"
	else
		replace_atomically "${tmp}" "${SITES_DIR}/apps.txt"
	fi
}

ensure_common_site_config() {
	local config="${SITES_DIR}/common_site_config.json"
	if [[ ! -s "${config}" ]]; then
		local tmp
		tmp="$(mktemp "${SITES_DIR}/.common_site_config.XXXXXX")"
		echo '{}' >"${tmp}"
		chmod 0640 "${tmp}"
		# Another container may have written it meanwhile; never overwrite real content.
		if [[ -s "${config}" ]]; then
			rm -f -- "${tmp}"
		else
			replace_atomically "${tmp}" "${config}"
		fi
	fi
}

prepare_bench() {
	if [[ ! -d "${SITES_DIR}" ]]; then
		log "sites directory ${SITES_DIR} is missing; mount the sites volume"
		exit 64
	fi
	if [[ ! -w "${SITES_DIR}" ]]; then
		log "sites directory ${SITES_DIR} is not writable by uid $(id -u)"
		exit 64
	fi
	link_assets
	write_apps_txt
	ensure_common_site_config
	mkdir -p "${BENCH_DIR}/logs"
}

worker_queues() {
	# worker_queues <a,b,c>: the queues bench knows (short, default, long and those defined
	# under "workers" in common_site_config.json). bench refuses to start a worker for an
	# undefined queue, and a crash-looping worker takes its other queues down with it, so
	# an undefined one is left out with a warning instead; the application then delivers
	# what it would have queued there by other means (alerts are sent inline).
	env/bin/python - "$1" <<'PY'
import json
import sys

known = {"short", "default", "long"}
try:
	with open("sites/common_site_config.json") as handle:
		workers = json.load(handle).get("workers")
	if isinstance(workers, dict):
		known.update(workers)
except (OSError, ValueError):
	pass
wanted = []
for queue in sys.argv[1].split(","):
	queue = queue.strip()
	if queue and queue not in wanted:
		wanted.append(queue)
missing = [queue for queue in wanted if queue not in known]
if missing:
	print(
		"h360-entrypoint: queue " + ", ".join(missing) + " is not defined in common_site_config.json "
		"(workers); starting without it",
		file=sys.stderr,
	)
print(",".join(queue for queue in wanted if queue in known) or "short,default")
PY
}

require_site() {
	if [[ -z "${SITE_NAME:-}" ]]; then
		log "SITE_NAME is not set"
		exit 64
	fi
}

role="${1:-backend}"
if [[ $# -gt 0 ]]; then
	shift
fi

case "${role}" in
	backend | websocket | worker | scheduler | frontend | configure | migrate)
		prepare_bench
		printf '%s\n' "${role}" >"${ROLE_FILE}" 2>/dev/null || true
		;;
	*)
		prepare_bench
		exec "${role}" "$@"
		;;
esac

cd "${BENCH_DIR}"

case "${role}" in
	backend)
		exec h360-backend "$@"
		;;
	websocket)
		exec node "${BENCH_DIR}/apps/frappe/socketio.js" "$@"
		;;
	worker)
		queues="$(worker_queues "${1:-${WORKER_QUEUES:-short,default}}")"
		log "starting worker for queues ${queues}"
		exec bench worker --queue "${queues}"
		;;
	scheduler)
		exec bench schedule
		;;
	frontend)
		exec h360-frontend "$@"
		;;
	configure)
		exec h360-configure "$@"
		;;
	migrate)
		require_site
		log "migrating ${SITE_NAME}"
		exec bench --site "${SITE_NAME}" migrate "$@"
		;;
esac
