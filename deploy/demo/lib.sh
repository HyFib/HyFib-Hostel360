# shellcheck shell=bash
# shellcheck disable=SC2034 # the paths are used by the scripts that source this file
# Shared by up.sh, down.sh, reset.sh and compose.sh (sourced, not run). Written for the bash
# that ships with macOS (3.2) as well as a current one.
#
# H360_DEMO_HOME (default ~/hostel360-demo) holds everything that is not in the repository:
#
#   demo.env               settings; copied from deploy/demo/demo.env.example on the first run
#   config/runtime.env     image tag and paths, rewritten by up.sh on every run
#   config/secrets/        database root and Administrator passwords (files 0600, folder 0700)
#   config/waf/tls/        the WAF's internal certificate (in-stack HTTPS for print PDFs)
#   config/waf/lists/      denylist.conf and rate-exempt.conf (both empty for the demo)
#   config/cloudflared/    config.yml, rendered from cloudflared/config.yml.template
#   data/sites/            the bench sites, with the golden snapshot in <site>/private/demo
#   data/logs/             Frappe, WAF and ClamAV logs
#
# MariaDB, the Redis queue and the ClamAV signatures are in the Docker volumes
# hostel360-demo_mariadb, hostel360-demo_redis-queue and hostel360-demo_clamav.

demo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${demo_dir}/../.." && pwd)"
compose_dir="${repo_root}/deploy/compose"
project=hostel360-demo

demo_home="${H360_DEMO_HOME:-${HOME}/hostel360-demo}"
demo_env="${demo_home}/demo.env"
config_dir="${demo_home}/config"
data_dir="${demo_home}/data"
secrets_dir="${config_dir}/secrets"
runtime_env="${config_dir}/runtime.env"

die() {
	printf '%s: %s\n' "${prog:-demo}" "$*" >&2
	exit 1
}

say() {
	printf '==> %s\n' "$*" >&2
}

note() {
	printf '    %s\n' "$*" >&2
}

env_file_value() {
	# env_file_value <file> <key>: the key's last value in a compose env file, without
	# surrounding quotes or a trailing comment ("" when absent)
	local line value=""
	while IFS= read -r line || [[ -n "${line}" ]]; do
		line="${line%$'\r'}"
		case "${line}" in
			"$2="*) value="${line#"$2="}" ;;
		esac
	done <"$1"
	case "${value}" in
		\"*\")
			value="${value#\"}"
			value="${value%\"}"
			;;
		\'*\')
			value="${value#\'}"
			value="${value%\'}"
			;;
		*)
			value="${value%%[[:space:]]#*}"
			value="${value%"${value##*[![:space:]]}"}"
			;;
	esac
	printf '%s' "${value}"
}

demo_setting() {
	# demo_setting <key> [default]: the setting from demo.env, or the default when empty
	local value=""
	if [[ -f "${demo_env}" ]]; then
		value="$(env_file_value "${demo_env}" "$1")"
	fi
	printf '%s' "${value:-${2:-}}"
}

demo_compose() {
	# docker compose with the demo's project, files and settings. Variables of the same
	# names in the caller's shell would take precedence over the env files, so they are
	# dropped for this one command.
	local key
	(
		while IFS= read -r key; do
			unset "${key}"
		done < <(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "${demo_env}" "${runtime_env}" 2>/dev/null)
		exec docker compose -p "${project}" --project-directory "${compose_dir}" \
			-f "${compose_dir}/compose.yml" -f "${demo_dir}/compose.demo.yml" \
			--env-file "${demo_env}" --env-file "${runtime_env}" "$@"
	)
}

require_demo() {
	[[ -f "${demo_env}" && -f "${runtime_env}" ]] ||
		die "no demo set up in ${demo_home} (run deploy/demo/up.sh first, or set H360_DEMO_HOME)"
}

demo_compose_all() {
	# demo_compose, including the tunnel and its guard when a tunnel is configured, so that
	# ps, logs, stop and down cover them too
	if [[ -n "$(demo_setting TUNNEL_ID)" ]]; then
		demo_compose --profile tunnel "$@"
	else
		demo_compose "$@"
	fi
}
