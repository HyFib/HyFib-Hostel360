#!/usr/bin/env bash
# Campus egress addresses in the Ansible playbook (exemptions.yml): production refuses to
# go ahead without them (or with a private, documentation or over-wide range), and once
# given they reach the WAF rate-limit exemptions, fail2ban's ignoreip and stack.env.
#
#   deploy/tests/ansible/run.sh        ansible-playbook on the PATH, or Docker with the image
#                                      built from deploy/tests/Containerfile
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy="$(cd "${here}/../.." && pwd)"
image="${H360_CHECKS_IMAGE:-hostel360-deploy-checks:1}"
out="$(mktemp -d)"
trap 'rm -rf -- "${out}"' EXIT
chmod 0777 "${out}"
failures=0

playbook() {
	# playbook <extra vars as JSON>: runs exemptions.yml; prints its output
	if command -v ansible-playbook >/dev/null 2>&1; then
		ANSIBLE_ROLES_PATH="${deploy}/ansible/roles" H360_TEST_OUT="${out}" ANSIBLE_NOCOLOR=1 \
			ansible-playbook -i localhost, "${here}/exemptions.yml" -e "$1" 2>&1
	else
		docker run --rm -v "${deploy}:/work/deploy:ro" -v "${out}:/out" \
			-e ANSIBLE_ROLES_PATH=/work/deploy/ansible/roles -e H360_TEST_OUT=/out -e ANSIBLE_NOCOLOR=1 \
			-e ANSIBLE_LOCALHOST_WARNING=0 -e ANSIBLE_INVENTORY_UNPARSED_WARNING=0 \
			"${image}" ansible-playbook -i localhost, /work/deploy/tests/ansible/exemptions.yml -e "$1" 2>&1
	fi
}

check() {
	# check <name> <expect: pass|fail> <extra vars JSON> [text the failure must mention]
	local name="$1" expect="$2" vars="$3" text="${4:-}" output status=0
	output="$(playbook "${vars}")" || status=$?
	if [[ "${expect}" == pass && "${status}" -eq 0 ]]; then
		echo "ok    ${name}"
	elif [[ "${expect}" == fail && "${status}" -ne 0 && "${output}" == *"${text}"* ]]; then
		echo "ok    ${name}"
	else
		echo "FAIL  ${name} (exit ${status}, expected ${expect})"
		grep -E 'FAILED|fatal|msg' <<<"${output}" | head -n 8 | sed 's/^/      /'
		failures=$((failures + 1))
	fi
}

check "production with campus addresses: exempt at the WAF, in fail2ban and in stack.env" pass \
	'{"h360_environment": "production", "h360_campus_egress_cidrs": ["117.239.42.16/29", "14.139.180.5"], "h360_waf_rate_exempt_cidrs": ["14.139.180.5", "152.67.160.0/28"], "h360_fail2ban_ignoreip": ["152.67.160.0/28"]}'
check "production with an IPv6 campus prefix" pass \
	'{"h360_environment": "production", "h360_campus_egress_cidrs": ["2405:201:e01c:a000::/56"]}'
check "staging may leave the campus addresses empty" pass \
	'{"h360_environment": "staging", "h360_campus_egress_cidrs": []}'
check "staging may use a private load-generator range" pass \
	'{"h360_environment": "staging", "h360_campus_egress_cidrs": ["10.20.3.0/24"]}'
check "production without campus addresses stops" fail \
	'{"h360_environment": "production", "h360_campus_egress_cidrs": []}' "Set h360_campus_egress_cidrs"
check "production refuses the documentation placeholder" fail \
	'{"h360_environment": "production", "h360_campus_egress_cidrs": ["203.0.113.16/29"]}' "documentation address"
check "production refuses a private range" fail \
	'{"h360_environment": "production", "h360_campus_egress_cidrs": ["192.168.10.1"]}' "private"
check "an over-wide range is refused" fail \
	'{"h360_environment": "staging", "h360_campus_egress_cidrs": ["117.0.0.0/8"]}' "no wider than /16"
check "0.0.0.0/0 is refused" fail \
	'{"h360_environment": "staging", "h360_campus_egress_cidrs": ["0.0.0.0/0"]}' "no wider than /16"
check "a malformed address is refused" fail \
	'{"h360_environment": "production", "h360_campus_egress_cidrs": ["117.239.42.300"]}' "is not an IPv4/IPv6 address"
check "a single string instead of a list is refused" fail \
	'{"h360_environment": "production", "h360_campus_egress_cidrs": "117.239.42.16/29"}' "must be a YAML list"
check "an unknown environment is refused" fail \
	'{"h360_environment": "prod", "h360_campus_egress_cidrs": ["117.239.42.16/29"]}' "must be \"production\" or \"staging\""

exit "${failures}"
