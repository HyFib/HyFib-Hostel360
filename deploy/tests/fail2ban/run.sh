#!/usr/bin/env bash
# Checks the h360-auth fail2ban filter against WAF access-log lines (format "h360"):
# failed sign-ins (401) and refusals (403) count; throttled requests (429), mistyped input
# (417), successes and other paths never do, so a rate limit cannot escalate into a ban.
#
#   deploy/tests/fail2ban/run.sh       fail2ban-regex on the PATH, or Docker with the image
#                                      built from deploy/tests/Containerfile
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy="$(cd "${H360_DEPLOY_DIR:-${here}/../..}" && pwd)"
filter="${deploy}/ansible/roles/fail2ban/files/filter.d/h360-auth.conf"
image="${H360_CHECKS_IMAGE:-hostel360-deploy-checks:1}"

matched() {
	# matched <log file>: number of lines the filter matches
	local output
	if command -v fail2ban-regex >/dev/null 2>&1; then
		output="$(fail2ban-regex "$1" "${filter}")"
	else
		output="$(docker run --rm -v "$1:/t/log:ro" -v "${filter}:/t/filter.conf:ro" --entrypoint fail2ban-regex \
			"${image}" /t/log /t/filter.conf)"
	fi
	sed -n -E 's/^Lines: [0-9]+ lines, [0-9]+ ignored, ([0-9]+) matched.*/\1/p' <<<"${output}"
}

failures=0
expected="$(grep -c . "${here}/should-match.log")"
count="$(matched "${here}/should-match.log")"
if [[ "${count}" == "${expected}" ]]; then
	echo "ok    failed and refused sign-ins count (${count}/${expected} lines)"
else
	echo "FAIL  failed and refused sign-ins count: ${count:-?} of ${expected} lines matched"
	failures=$((failures + 1))
fi

count="$(matched "${here}/should-not-match.log")"
if [[ "${count}" == 0 ]]; then
	echo "ok    throttled (429), mistyped (417) and successful requests never count"
else
	echo "FAIL  throttled, mistyped or successful requests counted: ${count:-?} lines matched"
	failures=$((failures + 1))
fi
exit "${failures}"
