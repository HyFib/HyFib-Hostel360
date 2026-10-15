# shellcheck shell=bash
# Shared helpers for the Hostel360 host scripts (sourced, not executed).
# Installed at /usr/local/lib/hostel360/h360-lib.sh.
#
# Every script: set -Eeuo pipefail, one flock per job, messages to the journal with a
# priority (under systemd stderr is the journal; interactively they also go to logger).

H360_CONFIG_DIR="${H360_CONFIG_DIR:-/etc/hostel360}"
H360_STATE_DIR="${H360_STATE_DIR:-/var/lib/hostel360}"
H360_SPOOL_DIR="${H360_SPOOL_DIR:-/var/spool/hostel360}"
H360_LOCK_DIR="${H360_LOCK_DIR:-/run/lock}"
H360_TAG="${H360_TAG:-h360}"

# --------------------------------------------------------------------------- logging

_h360_log() {
	local priority="$1" level="$2"
	shift 2
	local message="$*"
	if [[ -n "${JOURNAL_STREAM:-}" ]]; then
		# journald reads the <N> prefix as the syslog priority of the line
		printf '<%s>%s\n' "${priority}" "${message}" >&2
	else
		printf '%s %s[%s]: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${H360_TAG}" "${level}" "${message}" >&2
		if command -v logger >/dev/null 2>&1; then
			logger -t "${H360_TAG}" -p "user.${level}" -- "${message}" 2>/dev/null || true
		fi
	fi
}

log_info() { _h360_log 6 info "$@"; }
log_notice() { _h360_log 5 notice "$@"; }
log_warn() { _h360_log 4 warning "$@"; }
log_error() { _h360_log 3 err "$@"; }

die() {
	log_error "$@"
	exit 1
}

# --------------------------------------------------------------------------- setup

h360_init() {
	# h360_init <tag>: name used in the journal and for the lock file
	H360_TAG="$1"
	umask 0027
	export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
	export LC_ALL=C.UTF-8
	trap '_h360_on_error ${LINENO} "${BASH_COMMAND}"' ERR
}

_h360_on_error() {
	log_error "failed at line $1: $2"
}

h360_require_root() {
	[[ "${EUID}" -eq 0 ]] || die "run as root (sudo $0)"
}

h360_load_env() {
	# h360_load_env <file> [optional]: export KEY=VALUE lines from a root-owned file
	local file="$1" optional="${2:-}"
	if [[ ! -r "${file}" ]]; then
		[[ -n "${optional}" ]] && return 0
		die "configuration ${file} is missing (see deploy/host/config/*.example)"
	fi
	local owner mode
	owner="$(stat -c '%u' "${file}")"
	mode="$(stat -c '%a' "${file}")"
	if [[ "${owner}" != 0 || "${mode: -1}" != 0 ]]; then
		die "${file} must be owned by root and not readable by others (found uid ${owner}, mode ${mode})"
	fi
	set -a
	# shellcheck disable=SC1090
	source "${file}"
	set +a
}

declare -gA H360_LOCK_FDS=()

h360_lock() {
	# h360_lock <name> [wait-seconds]: hold an exclusive lock for the rest of the script.
	# Without a wait, a second run exits quietly; with one, it waits and then fails.
	local name="$1" wait="${2:-0}" fd
	mkdir -p "${H360_LOCK_DIR}"
	exec {fd}>"${H360_LOCK_DIR}/h360-${name}.lock"
	H360_LOCK_FDS["${name}"]="${fd}"
	if [[ "${wait}" -gt 0 ]]; then
		flock -w "${wait}" "${fd}" || die "another ${name} run holds the lock (waited ${wait}s)"
	else
		flock -n "${fd}" || {
			log_notice "another ${name} run is in progress; skipping"
			exit 0
		}
	fi
}

h360_try_lock() {
	# h360_try_lock <name> <wait-seconds>: like h360_lock, but returns 1 instead of ending
	# the script when another run still holds the lock after the wait.
	local name="$1" wait="${2:-0}" fd
	mkdir -p "${H360_LOCK_DIR}"
	exec {fd}>"${H360_LOCK_DIR}/h360-${name}.lock"
	if flock -w "${wait}" "${fd}"; then
		H360_LOCK_FDS["${name}"]="${fd}"
		return 0
	fi
	exec {fd}>&-
	return 1
}

h360_unlock() {
	# h360_unlock <name>: release a lock taken with h360_lock before the script ends
	local fd="${H360_LOCK_FDS[$1]:-}"
	[[ -n "${fd}" ]] || return 0
	flock -u "${fd}"
	exec {fd}>&-
	unset 'H360_LOCK_FDS[$1]'
}

h360_require_cmd() {
	local cmd
	for cmd in "$@"; do
		command -v "${cmd}" >/dev/null 2>&1 || die "required command not found: ${cmd}"
	done
}

h360_host() {
	printf '%s' "${H360_HOST_LABEL:-$(hostname -s)}"
}

h360_now_iso() {
	date '+%Y-%m-%dT%H:%M:%S%z'
}

# --------------------------------------------------------------------------- docker stack

h360_compose() {
	# H360_COMPOSE_FILE may list several files separated by ":" (a site-specific override
	# after the main file), like COMPOSE_FILE.
	local -a files=() args=()
	local file
	IFS=: read -r -a files <<<"${H360_COMPOSE_FILE:-/opt/hostel360/deploy/compose/compose.yml}"
	for file in "${files[@]}"; do
		args+=(--file "${file}")
	done
	# Shell variables win over --env-file in compose; the scripts' own settings
	# (agent.env) must never redirect the stack's volumes, so stack.env decides those.
	env -u H360_DATA_DIR -u H360_CONFIG_DIR -u H360_RUNTIME_DIR \
		docker compose \
		--project-name "${H360_COMPOSE_PROJECT:-hostel360}" \
		--env-file "${H360_STACK_ENV:-${H360_CONFIG_DIR}/stack.env}" \
		"${args[@]}" \
		"$@"
}

h360_container() {
	# h360_container <service>: id of the first running container of a compose service
	docker ps --quiet \
		--filter "label=com.docker.compose.project=${H360_COMPOSE_PROJECT:-hostel360}" \
		--filter "label=com.docker.compose.service=$1" \
		--filter "status=running" | head -n 1
}

h360_stack_value() {
	# h360_stack_value <KEY>: value of KEY in stack.env
	local file="${H360_STACK_ENV:-${H360_CONFIG_DIR}/stack.env}"
	sed -n "s/^$1=//p" "${file}" | tail -n 1
}

h360_bench() {
	# h360_bench <args...>: bench inside the running backend container
	local backend
	backend="$(h360_container backend)"
	[[ -n "${backend}" ]] || die "the backend container is not running"
	docker exec -i "${backend}" bench "$@"
}

h360_site_databases() {
	# Database names of every site on the sites volume, one per line
	local config
	for config in "${H360_DATA_DIR:-/srv/hostel360}"/sites/*/site_config.json; do
		[[ -f "${config}" ]] || continue
		jq -r '.db_name // empty' "${config}"
	done | sort -u
}

# --------------------------------------------------------------------------- reporting

h360_report() {
	# h360_report <json>: deliver to the Hostel360 ingest API (spooled when unreachable)
	local status=0
	printf '%s' "$1" | /usr/local/sbin/h360-ingest - || status=$?
	case "${status}" in
		0) ;;
		2) log_warn "report rejected by the ingest API; see Error Log in Hostel360" ;;
		*) log_warn "report not delivered now; it is spooled for the next attempt" ;;
	esac
}

h360_alert() {
	# h360_alert <key> <severity> <category> <title> <description> [is_outage]
	local payload
	payload="$(jq -cn \
		--arg key "$1" --arg severity "$2" --arg category "$3" \
		--arg title "$4" --arg description "$5" --arg host "$(h360_host)" \
		--argjson outage "${6:-false}" \
		'{type: "alert", source: "Host Agent", key: $key, severity: $severity, category: $category,
		  title: $title, description: $description, is_outage: $outage, details: {host: $host}}')"
	h360_report "${payload}"
}

h360_resolve() {
	# h360_resolve <key> <notes>
	local payload
	payload="$(jq -cn --arg key "$1" --arg notes "$2" '{type: "resolve", source: "Host Agent", key: $key, notes: $notes}')"
	h360_report "${payload}"
}

# --------------------------------------------------------------------------- database and site

h360_db_exec() {
	# h360_db_exec <mariadb client args...>: run the client as root in the mariadb container;
	# the password is read from the Docker secret inside the container.
	local mariadb
	mariadb="$(h360_container mariadb)"
	[[ -n "${mariadb}" ]] || die "the mariadb container is not running"
	# shellcheck disable=SC2016 # expanded by the shell inside the container
	docker exec -i "${mariadb}" sh -c 'MYSQL_PWD="$(cat /run/secrets/db_root_password)" exec mariadb --user=root "$@"' sh "$@"
}

h360_dump_databases() {
	# h360_dump_databases <file.sql.gz>: consistent dump of every site database
	local file="$1" mariadb
	local -a databases
	mariadb="$(h360_container mariadb)"
	[[ -n "${mariadb}" ]] || return 1
	mapfile -t databases < <(h360_site_databases)
	[[ "${#databases[@]}" -gt 0 ]] || return 1
	# shellcheck disable=SC2016 # expanded by the shell inside the container
	docker exec "${mariadb}" sh -c 'MYSQL_PWD="$(cat /run/secrets/db_root_password)" exec mariadb-dump --user=root \
		--single-transaction --quick --routines --triggers --events --hex-blob --default-character-set=utf8mb4 \
		--databases "$@"' sh "${databases[@]}" | gzip -6 >"${file}"
	gzip -t "${file}"
}

h360_drop_site_databases() {
	local db
	while IFS= read -r db; do
		[[ "${db}" =~ ^[A-Za-z0-9_]+$ ]] || continue
		h360_db_exec -e "DROP DATABASE IF EXISTS \`${db}\`"
	done < <(h360_site_databases)
}

h360_maintenance() {
	# h360_maintenance <site> on|off
	h360_bench --site "$1" set-maintenance-mode "$2" >/dev/null 2>&1 ||
		log_warn "could not switch maintenance mode $2 for $1"
}

h360_wait_for_boot() {
	# h360_wait_for_boot <site>: gunicorn answers (503 in maintenance mode, 200 otherwise)
	local site="$1" backend code i
	for i in $(seq 1 60); do
		backend="$(h360_container backend)"
		if [[ -n "${backend}" ]]; then
			code="$(docker exec "${backend}" curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
				-H "Host: ${site}" -H "X-Frappe-Site-Name: ${site}" \
				http://127.0.0.1:8000/api/method/hostel360.api.health.liveness 2>/dev/null || true)"
			if [[ "${code}" == 200 || "${code}" == 503 ]]; then
				return 0
			fi
		fi
		[[ "${i}" -lt 60 ]] && sleep 3
	done
	return 1
}

h360_smoke_check() {
	# h360_smoke_check <site> [port]: readiness three times in a row, then the login page,
	# through the WAF on this host exactly as the load balancer reaches it. Web workers
	# cache the site configuration for 60 s, so a maintenance-mode switch can take that
	# long to show; the checks keep trying for two minutes.
	local site="$1" port="${H360_LOCAL_WAF_PORT:-${2:-8443}}" ok=0 i body address
	address="${H360_LOCAL_WAF_ADDRESS:-127.0.0.1}"
	if [[ ! "${address}" =~ ^[0-9.]+$ ]]; then
		# A host name is resolved on every call: the address can change when the WAF restarts.
		address="$(getent ahostsv4 "${address}" | awk 'NR == 1 { print $1 }')"
	fi
	local -a tls=(--resolve "${site}:${port}:${address:-127.0.0.1}")
	[[ -f "${H360_CONFIG_DIR}/waf/tls/server.crt" ]] && tls+=(--cacert "${H360_CONFIG_DIR}/waf/tls/server.crt")
	local base="https://${site}:${port}"
	for i in $(seq 1 40); do
		body="$(curl -fsS --max-time 10 "${tls[@]}" "${base}/api/method/hostel360.api.health.readiness" 2>/dev/null || true)"
		if [[ "$(jq -r '.message.status // empty' <<<"${body}" 2>/dev/null || true)" == ok ]]; then
			ok=$((ok + 1))
			[[ "${ok}" -ge 3 ]] && break
		else
			ok=0
		fi
		[[ "${i}" -lt 40 ]] && sleep 3
	done
	[[ "${ok}" -ge 3 ]] || return 1
	curl -fsS -o /dev/null --max-time 10 "${tls[@]}" "${base}/login"
}
