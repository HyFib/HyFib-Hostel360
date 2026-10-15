#!/usr/bin/env bash
# Runs the Hostel360 load test and keeps its summary in tests/load/results/.
#
#   tests/load/run.sh smoke|load [extra k6 arguments]
#
# Settings come from the environment (README.md): BASE_URL, OTP_CODE, MOBILES_FILE,
# DESK_API_KEY, DESK_API_SECRET, SITE_HOST, PORTAL_VUS, DESK_VUS, RAMP_UP, HOLD, RAMP_DOWN,
# THINK_MIN, THINK_MAX, MIN_PEAK_RATE, RESULT_LABEL. Values are handed to k6 through the
# environment, never on a command line.
#
# Uses k6 from the PATH when present, otherwise the pinned grafana/k6 image; K6_NETWORK picks
# the container's network (for example container:<name> to reach a server on its localhost).
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
profile="${1:-}"
case "${profile}" in
	smoke | load) shift ;;
	*)
		echo "usage: $0 smoke|load [extra k6 arguments]" >&2
		exit 64
		;;
esac

k6_image="${K6_IMAGE:-grafana/k6:2.3.0}"
label="${RESULT_LABEL:-$(sed -E 's#^https?://##; s#[/:]+#-#g; s#-+$##' <<<"${BASE_URL:-unset}")}"
stamp="$(date +%Y%m%d-%H%M%S)"
results="${here}/results"
name="${stamp}-${profile}-${label}"
mkdir -p "${results}"

vars=(BASE_URL OTP_CODE OTP_CHANNEL DESK_API_KEY DESK_API_SECRET SITE_HOST PORTAL_VUS DESK_VUS RAMP_UP HOLD RAMP_DOWN
	THINK_MIN THINK_MAX MIN_PEAK_RATE PORTAL_LANGUAGE)

export PROFILE="${profile}"
status=0
if [[ "${K6_DOCKER:-0}" != 1 ]] && command -v k6 >/dev/null 2>&1; then
	k6 run --quiet --summary-export "${results}/${name}.json" "$@" "${here}/hostel360.js" 2>&1 |
		tee "${results}/${name}.txt" || status=$?
else
	env_args=(-e PROFILE)
	for var in "${vars[@]}"; do
		[[ -n "${!var:-}" ]] && env_args+=(-e "${var}")
	done
	mounts=(-v "${here}:/scripts:ro" -v "${results}:/results")
	if [[ -n "${MOBILES_FILE:-}" ]]; then
		[[ -r "${MOBILES_FILE}" ]] || {
			echo "MOBILES_FILE ${MOBILES_FILE} is not readable" >&2
			exit 66
		}
		mounts+=(-v "$(cd "$(dirname "${MOBILES_FILE}")" && pwd)/$(basename "${MOBILES_FILE}"):/data/mobiles.txt:ro")
		env_args+=(-e MOBILES_FILE=/data/mobiles.txt)
	fi
	docker run --rm -i --user "$(id -u):$(id -g)" --network "${K6_NETWORK:-bridge}" \
		"${mounts[@]}" "${env_args[@]}" "${k6_image}" \
		run --quiet --summary-export "/results/${name}.json" "$@" /scripts/hostel360.js 2>&1 |
		tee "${results}/${name}.txt" || status=$?
fi

# k6 exits 99 when a threshold failed; the summary is kept either way.
echo
echo "summary: ${results}/${name}.txt and ${name}.json (exit ${status})"
exit "${status}"
