#!/usr/bin/env bash
# Start, or update, the Hostel360 public demo on this workstation.
#
#   deploy/demo/up.sh                    build the image from this checkout, start the stack,
#                                        create and prepare the demo site, open the tunnel
#   deploy/demo/up.sh --no-build         run the image already built for this checkout
#   deploy/demo/up.sh --no-tunnel        everything but the tunnel: reachable on this Mac only
#
# Run it again after "git pull" to move the demo to the new commit: it builds the new image,
# recreates the containers, migrates the site and prepares the demo again. The public URL is
# offline from the start of a run until the site has passed every check at the end; the
# tunnel only ever opens for a site in demo mode.
#
# Settings: ~/hostel360-demo/demo.env (H360_DEMO_HOME), created from demo.env.example on the
# first run. See deploy/demo/README.md.
set -Eeuo pipefail

prog=up.sh
# shellcheck source=deploy/demo/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() {
	sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'
	cat <<'EOF'

Options:
  --no-build         use the image already built for this checkout (hostel360:<version>-<commit>)
  --image-tag TAG    use hostel360:TAG instead of building
  --no-tunnel        do not open the tunnel
  --skip-demo-site   do not run "bench h360-demo-site" (with --no-tunnel only: the site is
                     then not a demo and must never be published)
  -h, --help         show this help
EOF
}

build=1
image_tag=""
open_tunnel=1
prepare_demo=1
while [[ $# -gt 0 ]]; do
	case "$1" in
		--no-build) build=0 ;;
		--image-tag)
			image_tag="${2:?--image-tag needs a value}"
			build=0
			shift
			;;
		--no-tunnel) open_tunnel=0 ;;
		--skip-demo-site) prepare_demo=0 ;;
		-h | --help)
			usage
			exit 0
			;;
		*) die "unknown option: $1 (see --help)" ;;
	esac
	shift
done
if [[ "${prepare_demo}" == 0 && "${open_tunnel}" == 1 ]]; then
	die "--skip-demo-site leaves the site without demo mode; combine it with --no-tunnel"
fi

for tool in docker git openssl curl lsof sed; do
	command -v "${tool}" >/dev/null || die "${tool} is not installed"
done
docker info >/dev/null 2>&1 || die "Docker is not running (start Docker Desktop)"
compose_version="$(docker compose version --short 2>/dev/null || true)"
compose_version="${compose_version#v}"
compose_major="${compose_version%%.*}"
compose_minor="${compose_version#*.}"
compose_minor="${compose_minor%%.*}"
# Two env files and the !reset tag need Compose 2.24 or later.
if [[ ! "${compose_major}" =~ ^[0-9]+$ || ! "${compose_minor}" =~ ^[0-9]+$ ]] ||
	((compose_major < 2 || (compose_major == 2 && compose_minor < 24))); then
	die "Docker Compose 2.24 or later is needed (found '${compose_version:-none}')"
fi

# --------------------------------------------------------------------------- settings

# Secrets and data live outside the checkout, so they can never be committed.
case "${demo_home}/" in
	"${repo_root}/"*) die "H360_DEMO_HOME (${demo_home}) must be outside the repository" ;;
esac

mkdir -p "${config_dir}/waf/tls" "${config_dir}/waf/lists" "${config_dir}/cloudflared" "${secrets_dir}" \
	"${data_dir}/sites" "${data_dir}/logs/frappe" "${data_dir}/logs/waf" "${data_dir}/logs/clamav"
chmod 0700 "${demo_home}" "${config_dir}" "${secrets_dir}" "${config_dir}/waf/tls"

if [[ ! -f "${demo_env}" ]]; then
	(umask 0077 && cp "${demo_dir}/demo.env.example" "${demo_env}")
	say "created ${demo_env} from deploy/demo/demo.env.example"
	note "set TUNNEL_ID there once the tunnel exists (deploy/demo/README.md); until then the demo runs on this Mac only"
fi

site="$(demo_setting SITE_NAME)"
[[ "${site}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] ||
	die "SITE_NAME in ${demo_env} must be a lowercase host name (got '${site}')"
public_url="$(demo_setting PUBLIC_URL "https://${site}")"
[[ "${public_url}" == "https://${site}" ]] ||
	die "PUBLIC_URL must be https://${site} (the tunnel serves exactly that address)"

bind_address="$(demo_setting WAF_BIND_ADDRESS 127.0.0.1)"
[[ "${bind_address}" =~ ^127\.[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
	die "WAF_BIND_ADDRESS must be a loopback address such as 127.0.0.1 (got '${bind_address}'); visitors use the tunnel, never the LAN"
http_port="$(demo_setting WAF_HTTP_PORT 18080)"
https_port="$(demo_setting WAF_HTTPS_PORT 18443)"
for port in "${http_port}" "${https_port}"; do
	[[ "${port}" =~ ^[1-9][0-9]{2,4}$ ]] || die "WAF_HTTP_PORT and WAF_HTTPS_PORT must be port numbers (got '${port}')"
done

tunnel_ip="$(demo_setting H360_TUNNEL_IP)"
[[ "${tunnel_ip}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
	die "H360_TUNNEL_IP in ${demo_env} must be an IPv4 address inside H360_EDGE_SUBNET (got '${tunnel_ip}')"

tunnel_id="$(demo_setting TUNNEL_ID)"
credentials=""
if [[ -n "${tunnel_id}" ]]; then
	[[ "${tunnel_id}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ||
		die "TUNNEL_ID must be the tunnel's UUID as 'cloudflared tunnel list' shows it (got '${tunnel_id}')"
	credentials="$(demo_setting CLOUDFLARED_CREDENTIALS_FILE "${HOME}/.cloudflared/${tunnel_id}.json")"
	# A path written as ~/... in demo.env (a literal tilde there)
	# shellcheck disable=SC2088
	case "${credentials}" in
		"~/"*) credentials="${HOME}/${credentials#"~/"}" ;;
	esac
	case "${credentials}" in
		"${repo_root}"/*) die "CLOUDFLARED_CREDENTIALS_FILE must stay outside the repository (${credentials})" ;;
	esac
elif [[ "${open_tunnel}" == 1 ]]; then
	note "TUNNEL_ID is empty in ${demo_env}: starting without the tunnel"
	open_tunnel=0
fi
if [[ "${open_tunnel}" == 1 && ! -r "${credentials}" ]]; then
	die "the tunnel credentials ${credentials} are missing; create the tunnel first (deploy/demo/README.md)"
fi

students="$(demo_setting DEMO_STUDENTS)"
seed="$(demo_setting DEMO_SEED)"
for value in "${students}" "${seed}"; do
	[[ -z "${value}" || "${value}" =~ ^[0-9]+$ ]] || die "DEMO_STUDENTS and DEMO_SEED must be whole numbers"
done

# --------------------------------------------------------------------------- secrets, WAF files

new_secret() {
	# new_secret <file>: a random password in <file> (0600), kept once it exists
	if [[ ! -s "$1" ]]; then
		(umask 0077 && openssl rand -hex 24 | tr -d '\n' >"$1.tmp" && mv -f "$1.tmp" "$1")
		note "created $1"
	fi
	chmod 0600 "$1"
}
new_secret "${secrets_dir}/db_root_password"
new_secret "${secrets_dir}/site_admin_password"

waf_image="$(sed -n -E 's/^ +image: (owasp\/modsecurity-crs:[^ ]+)$/\1/p' "${compose_dir}/compose.yml" | head -n 1)"
cloudflared_image="$(sed -n -E 's/^ +image: (cloudflare\/cloudflared:[^ ]+)$/\1/p' "${demo_dir}/compose.demo.yml" | head -n 1)"
[[ -n "${waf_image}" && -n "${cloudflared_image}" ]] || die "could not read the WAF and cloudflared images from the compose files"

tls_dir="${config_dir}/waf/tls"
if [[ ! -s "${tls_dir}/server.crt" || ! -s "${tls_dir}/server.key" ]]; then
	# In-stack HTTPS only (print PDFs load the logo through the WAF on 443); visitors see
	# Cloudflare's certificate.
	docker run --rm --user "$(id -u):$(id -g)" -v "${tls_dir}:/tls" --entrypoint openssl "${waf_image}" \
		req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 825 \
		-subj "/CN=${site}" -addext "subjectAltName=DNS:${site}" \
		-keyout /tls/server.key -out /tls/server.crt >/dev/null 2>&1 ||
		die "could not create the WAF's internal certificate"
	chmod 0600 "${tls_dir}/server.key"
	chmod 0644 "${tls_dir}/server.crt"
	note "created the WAF's internal certificate for ${site}"
fi
for list in denylist rate-exempt; do
	if [[ ! -f "${config_dir}/waf/lists/${list}.conf" ]]; then
		printf '# Empty for the public demo: no bans, and every visitor has the per-address limits.\n' \
			>"${config_dir}/waf/lists/${list}.conf"
	fi
done

# --------------------------------------------------------------------------- image

version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${repo_root}/hostel360/__init__.py")"
[[ -n "${version}" ]] || die "could not read __version__ from hostel360/__init__.py"
if [[ -z "${image_tag}" ]]; then
	# The tag deploy/docker/build.sh gives this checkout
	image_tag="${version}-$(git -C "${repo_root}" rev-parse --short=12 HEAD)"
	if [[ -n "$(git -C "${repo_root}" status --porcelain)" ]]; then
		image_tag="${image_tag}-dirty"
	fi
fi
if [[ "${build}" == 1 ]]; then
	say "building hostel360:${image_tag} from this checkout"
	"${repo_root}/deploy/docker/build.sh" --image hostel360
fi
docker image inspect "hostel360:${image_tag}" >/dev/null 2>&1 ||
	die "image hostel360:${image_tag} does not exist (run without --no-build, or pass --image-tag)"

# --------------------------------------------------------------------------- runtime.env

tmp_env="$(mktemp "${config_dir}/.runtime.env.XXXXXX")"
cat >"${tmp_env}" <<EOF
# Written by deploy/demo/up.sh on $(date '+%Y-%m-%d %H:%M %Z'); rewritten on every run.
# Settings belong in ${demo_env}.
H360_IMAGE=hostel360
H360_IMAGE_TAG=${image_tag}
H360_DATA_DIR=${data_dir}
H360_CONFIG_DIR=${config_dir}
H360_RUNTIME_DIR=${demo_home}/run
LB_SUBNET_CIDR=${tunnel_ip}/32
CLOUDFLARED_CREDENTIALS_FILE=${credentials:-/nonexistent/cloudflared-credentials.json}
EOF
chmod 0600 "${tmp_env}"
mv -f "${tmp_env}" "${runtime_env}"

demo_compose config --quiet || die "the compose configuration is not valid (see above)"

running() {
	# running <service>: true when a container of the service is running
	[[ -n "$(demo_compose_all ps -q --status running "$1" 2>/dev/null)" ]]
}

if ! running waf; then
	for port in "${http_port}" "${https_port}"; do
		if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
			die "port ${port} on this Mac is already in use ($(lsof -nP -iTCP:"${port}" -sTCP:LISTEN | awk 'NR == 2 {print $1}')); choose another WAF_HTTP_PORT / WAF_HTTPS_PORT in ${demo_env}"
		fi
	done
fi

# --------------------------------------------------------------------------- stack

if [[ -n "${tunnel_id}" ]] && running tunnel; then
	say "closing the tunnel while the stack is updated (the public URL is offline until the end)"
	demo_compose --profile tunnel stop tunnel >/dev/null
fi

wait_for() {
	# wait_for <healthy|running|completed> <seconds> <service>...
	local condition="$1" limit="$2" deadline service ids id state health code pending
	shift 2
	deadline=$((SECONDS + limit))
	while true; do
		pending=""
		for service in "$@"; do
			ids="$(demo_compose_all ps -a -q "${service}" 2>/dev/null || true)"
			if [[ -z "${ids}" ]]; then
				pending="${pending} ${service}"
				continue
			fi
			for id in ${ids}; do
				read -r state health code <<<"$(docker inspect -f \
					'{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.State.ExitCode}}' "${id}")"
				case "${condition}:${state}" in
					completed:exited)
						[[ "${code}" == 0 ]] || die "${service} failed (exit status ${code}); see: deploy/demo/compose.sh logs ${service}"
						;;
					completed:*) pending="${pending} ${service}" ;;
					running:running) ;;
					healthy:running) [[ "${health}" == healthy ]] || pending="${pending} ${service}" ;;
					*:exited | *:dead)
						die "${service} stopped (exit status ${code}); see: deploy/demo/compose.sh logs ${service}"
						;;
					*) pending="${pending} ${service}" ;;
				esac
			done
		done
		if [[ -z "${pending}" ]]; then
			return 0
		fi
		if ((SECONDS >= deadline)); then
			note "still waiting for:${pending}"
			return 1
		fi
		sleep 5
	done
}

bench_site() {
	demo_compose exec -T backend bench --site "${site}" "$@"
}

say "starting the stack (project ${project}, image hostel360:${image_tag})"
demo_compose up -d --remove-orphans

wait_for healthy 300 mariadb redis-cache redis-queue || die "the database and Redis did not become healthy"
wait_for completed 300 configurator || die "the configurator did not finish"
wait_for running 120 backend demo-reset || die "the backend did not start"

site_state="${config_dir}/site-image-tag"
if [[ ! -f "${data_dir}/sites/${site}/site_config.json" ]]; then
	say "creating ${site} with hostel360 (a few minutes)"
	# Line 1: database root password, line 2: Administrator password; never on the host's
	# command line. The script runs in the container, hence the single quotes.
	# shellcheck disable=SC2016
	{
		cat "${secrets_dir}/db_root_password"
		echo
		cat "${secrets_dir}/site_admin_password"
		echo
	} | demo_compose exec -T backend bash -c '
		set -euo pipefail
		IFS= read -r db_root
		IFS= read -r admin
		bench new-site "$1" --mariadb-user-host-login-scope=% \
			--db-root-password "${db_root}" --admin-password "${admin}" --install-app hostel360
	' bash "${site}"
	# The scheduler stays paused (site_config.json) until the site is in demo mode, but is
	# enabled in System Settings now, so the golden snapshot carries it enabled.
	bench_site scheduler pause >/dev/null
	bench_site scheduler enable >/dev/null
	# host_name (print PDFs) and the alerts queue are set by the configurator once the site exists.
	if ! output="$(demo_compose run --rm --no-deps configurator 2>&1)"; then
		printf '%s\n' "${output}" >&2
		die "the configurator failed for the new site"
	fi
	printf '%s\n' "${image_tag}" >"${site_state}"
elif [[ "$(cat "${site_state}" 2>/dev/null || true)" != "${image_tag}" ]]; then
	say "migrating ${site} to hostel360:${image_tag}"
	bench_site migrate
	printf '%s\n' "${image_tag}" >"${site_state}"
fi

if [[ "${prepare_demo}" == 1 ]]; then
	bench_site h360-demo-site --help >/dev/null 2>&1 ||
		die "this image has no 'bench h360-demo-site'; the demo cannot be prepared from this commit"
	demo_args=()
	if [[ -n "${students}" ]]; then
		demo_args+=(--students "${students}")
	fi
	if [[ -n "${seed}" ]]; then
		demo_args+=(--seed "${seed}")
	fi
	say "preparing the demo data and its golden snapshot (bench h360-demo-site)"
	bench_site h360-demo-site ${demo_args[@]+"${demo_args[@]}"}
	demo_compose exec -T demo-reset bash /opt/hostel360-demo/demo-mode-check ||
		die "${site} is not in demo mode after h360-demo-site; the tunnel stays closed"
	bench_site scheduler resume >/dev/null
	bench_site scheduler enable >/dev/null
else
	note "h360-demo-site skipped: ${site} is not a demo, its scheduler stays paused and the tunnel closed"
fi

say "waiting for the application"
wait_for healthy 300 backend websocket frontend queue-short queue-long scheduler php-fpm waf ||
	die "the stack did not become healthy; see: deploy/demo/compose.sh ps"

readiness_url="http://${bind_address}:${http_port}/api/method/hostel360.api.health.readiness"
# h360-demo-site ends maintenance mode as its last step, and web workers read site_config.json
# at most a minute late: for that minute they still answer SessionStopped. Anything else fails
# at once.
for _ in $(seq 1 18); do
	readiness="$(curl -sS --max-time 15 -H "Host: ${site}" "${readiness_url}" 2>&1 || true)"
	[[ "${readiness}" == *SessionStopped* ]] || break
	sleep 5
done
[[ "${readiness}" == *'"status":"ok"'* || "${readiness}" == *'"status": "ok"'* ]] ||
	die "readiness through the WAF failed: ${readiness}"
note "readiness through the WAF: ${readiness}"

if ! wait_for healthy 10 clamav 2>/dev/null; then
	say "waiting for ClamAV to load its signatures (a minute or two; longer if freshclam has a lot to fetch)"
	wait_for healthy 1500 clamav ||
		die "ClamAV is not ready; uploads would be refused. Run deploy/demo/up.sh --no-build again later"
fi

# --------------------------------------------------------------------------- tunnel

if [[ "${open_tunnel}" == 1 ]]; then
	tunnel_config="${config_dir}/cloudflared/config.yml"
	sed -e "s/@TUNNEL_ID@/${tunnel_id}/g" -e "s/@SITE_NAME@/${site}/g" \
		"${demo_dir}/cloudflared/config.yml.template" >"${tunnel_config}.tmp"
	chmod 0644 "${tunnel_config}.tmp"
	mv -f "${tunnel_config}.tmp" "${tunnel_config}"
	docker run --rm -v "${tunnel_config}:/etc/cloudflared/config.yml:ro" "${cloudflared_image}" \
		tunnel --config /etc/cloudflared/config.yml ingress validate >/dev/null ||
		die "the tunnel configuration ${tunnel_config} is not valid"

	say "opening the tunnel"
	demo_compose --profile tunnel up -d tunnel
	wait_for healthy 180 tunnel || die "the tunnel did not connect; see: deploy/demo/compose.sh logs tunnel"
fi

# --------------------------------------------------------------------------- summary

printf '\nHyFib Hostel360 demo is running (hostel360:%s).\n\n' "${image_tag}"
if [[ "${open_tunnel}" == 1 ]]; then
	printf '  Public URL      %s\n' "${public_url}"
	printf '  Demo sign-ins   %s/demo\n' "${public_url}"
else
	printf '  Public URL      closed (no tunnel); local only\n'
fi
printf '  Local check     curl -H "Host: %s" %s\n' "${site}" "${readiness_url}"
printf '  Administrator   password in %s (not part of the demo)\n' "${secrets_dir}/site_admin_password"
printf '  Nightly reset   %s Asia/Kolkata (deploy/demo/compose.sh logs demo-reset)\n' "$(demo_setting DEMO_RESET_AT 02:30)"
printf '  Logs            deploy/demo/compose.sh logs -f backend waf%s\n' "$([[ "${open_tunnel}" == 1 ]] && printf ' tunnel')"
printf '  Stop            deploy/demo/down.sh\n\n'
