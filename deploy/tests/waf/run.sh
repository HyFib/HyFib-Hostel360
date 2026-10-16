#!/usr/bin/env bash
# Regression checks for the Hostel360 edge (deploy/waf) in the pinned WAF image.
#
#   deploy/tests/waf/run.sh            needs Docker with the compose plugin; about a minute
#
# The waf service is started from deploy/compose/compose.yml itself, with the same
# environment, templates and CRS exclusions as production; only its upstreams (frontend and
# PHP-FPM) are replaced by a small nginx stub that answers like the application does, so any
# 403 seen here comes from the WAF. Checks:
#
#   - the portal's Content Security Policy lets the fees page post to PayU's checkout
#   - visitors the edge received over plain HTTP are redirected to HTTPS
#   - PayU's return post (the payer's browser) and webhook pass ModSecurity, including bank
#     text the CRS would otherwise take for an attack (payu-return-failure.txt carries an
#     issuer message as an HTML fragment, which the CRS blocks without the exclusion)
#   - scanners and injection attempts are still blocked, on other endpoints and in the
#     headers of the PayU endpoints
#   - desk requests the CRS used to take for attacks pass (number cards, user settings,
#     realtime long polling), while the same rules still apply to other arguments and paths
#   - containers of the stack reach the portal host name (print PDFs load the logo and
#     other files through it) at the WAF, over https on 443 and http on 80
#
# H360_DEPLOY_DIR runs the checks against another copy of the deploy tree (for example an
# older release unpacked with "git archive"); exit status is the number of failed checks.
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy="$(cd "${H360_DEPLOY_DIR:-${here}/../..}" && pwd)"
compose_file="${deploy}/compose/compose.yml"
project="h360-waftest-$$"
site="portal.hostel360.test"

waf_image="$(sed -n -E 's/^ +image: (owasp\/modsecurity-crs:[^ ]+)$/\1/p' "${compose_file}" | head -n 1)"
[[ -n "${waf_image}" ]] || {
	echo "run.sh: no WAF image found in ${compose_file}" >&2
	exit 2
}

work="$(mktemp -d)"
failures=0

compose() {
	docker compose -p "${project}" \
		-f "${compose_file}" -f "${here}/compose.override.yml" \
		--env-file "${work}/stack.env" "$@"
}

# shellcheck disable=SC2329 # run by the EXIT trap
cleanup() {
	if [[ "${KEEP_STACK:-0}" != 1 ]]; then
		compose down --volumes --remove-orphans >/dev/null 2>&1 || true
		rm -rf -- "${work}"
	else
		echo "kept: project ${project}, files in ${work}"
	fi
}
trap cleanup EXIT

pass() { printf 'ok    %s\n' "$1"; }
fail() {
	printf 'FAIL  %s\n' "$1"
	[[ -z "${2:-}" ]] || printf '      %s\n' "$2"
	failures=$((failures + 1))
}

# --------------------------------------------------------------------------- set-up

mkdir -p "${work}/config/waf/tls" "${work}/config/waf/lists" "${work}/config/secrets" \
	"${work}/data/logs/waf" "${work}/run"
: >"${work}/config/waf/lists/denylist.conf"
: >"${work}/config/waf/lists/rate-exempt.conf"
: >"${work}/config/secrets/db_root_password"
# The WAF runs as uid 101; the throwaway files must be readable (and the log dir writable).
chmod -R a+rX "${work}"
chmod 0777 "${work}/data/logs/waf"

docker run --rm --user "$(id -u):$(id -g)" -v "${work}/config/waf/tls:/tls" --entrypoint openssl "${waf_image}" \
	req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 2 \
	-subj "/CN=${site}" -keyout /tls/server.key -out /tls/server.crt >/dev/null 2>&1
chmod 0644 "${work}/config/waf/tls/server.key" "${work}/config/waf/tls/server.crt"

cat >"${work}/stack.env" <<EOF
H360_IMAGE=hostel360-not-used
H360_IMAGE_TAG=0
H360_TEST_WAF_IMAGE=${waf_image}
H360_TEST_DIR=${here}
SITE_NAME=${site}
PUBLIC_URL=https://${site}
LB_SUBNET_CIDR=10.255.255.0/24
H360_DATA_DIR=${work}/data
H360_CONFIG_DIR=${work}/config
H360_RUNTIME_DIR=${work}/run
WAF_BIND_ADDRESS=127.0.0.1
WAF_HTTPS_PORT=${H360_TEST_HTTPS_PORT:-38443}
WAF_HTTP_PORT=${H360_TEST_HTTP_PORT:-38080}
WAF_RULE_ENGINE=On
H360_EDGE_SUBNET=${H360_TEST_EDGE_SUBNET:-172.28.231.0/24}
H360_INTERNAL_SUBNET=${H360_TEST_INTERNAL_SUBNET:-172.28.232.0/24}
H360_PHP_SUBNET=${H360_TEST_PHP_SUBNET:-172.28.233.0/24}
EOF

compose up -d --no-deps frontend php-fpm waf >/dev/null 2>&1 || {
	compose up -d --no-deps frontend php-fpm waf
	exit 2
}

edge="${project}_edge"
internal="${project}_internal"

# client <network> <curl arguments...>: curl from a container on that network; prints the
# response headers followed by "status=<code>".
client() {
	local network="$1"
	shift
	docker run --rm --network "${network}" --entrypoint curl "${waf_image}" \
		-sS -k -o /dev/null -D - -w 'status=%{http_code}\n' --max-time 20 "$@" 2>&1 || true
}

status_of() { sed -n 's/^status=//p' <<<"$1" | tail -n 1; }
header_of() { grep -i "^$2:" <<<"$1" | head -n 1 | cut -d: -f2- | sed 's/^ *//; s/\r$//'; }

# load_form <file>: form=(--data-urlencode "name=value" ...) from a fixture, one field per line
form=()
load_form() {
	local line
	form=()
	while IFS= read -r line || [[ -n "${line}" ]]; do
		if [[ -n "${line}" ]]; then
			form+=(--data-urlencode "${line}")
		fi
	done <"$1"
}

ready=0
for _ in $(seq 1 60); do
	if [[ "$(status_of "$(client "${edge}" -H "Host: ${site}" "http://waf:8080/healthz")")" == 200 ]]; then
		ready=1
		break
	fi
	sleep 2
done
if [[ "${ready}" != 1 ]]; then
	echo "run.sh: the WAF did not become ready" >&2
	compose logs waf 2>&1 | tail -n 40 >&2
	exit 2
fi

# --------------------------------------------------------------------------- checks

# Requests from the load balancer: plain HTTP to :8080 with the portal host name.
lb=(-H "Host: ${site}" -A "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/141.0 Mobile Safari/537.36"
	-H "Accept: text/html,application/json;q=0.9,*/*;q=0.8" -H "Accept-Language: en-IN,en;q=0.9")

response="$(client "${edge}" "${lb[@]}" "http://waf:8080/hostel/fees")"
csp="$(header_of "${response}" Content-Security-Policy)"
if [[ "$(status_of "${response}")" == 200 && "${csp}" == *"form-action 'self' https://api.razorpay.com https://secure.payu.in https://test.payu.in;"* ]]; then
	pass "portal CSP allows the PayU checkout form post (form-action)"
else
	fail "portal CSP allows the PayU checkout form post (form-action)" "status $(status_of "${response}"), CSP: ${csp}"
fi
if [[ "${csp}" == *"script-src 'self' https://checkout.razorpay.com;"* && "${csp}" != *"unsafe-inline' 'unsafe-eval"* ]]; then
	pass "portal CSP stays strict (no inline or evaluated scripts)"
else
	fail "portal CSP stays strict (no inline or evaluated scripts)" "CSP: ${csp}"
fi

response="$(client "${edge}" "${lb[@]}" "http://waf:8080/app/hostel-student")"
csp="$(header_of "${response}" Content-Security-Policy)"
if [[ "${csp}" == *"form-action 'self' https://api.razorpay.com;"* ]]; then
	pass "desk CSP unchanged (no payment form posts outside the portal)"
else
	fail "desk CSP unchanged (no payment form posts outside the portal)" "CSP: ${csp}"
fi

payu=(-H "Host: ${site}" -A "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/141.0 Mobile Safari/537.36"
	-H "Origin: https://secure.payu.in" -H "Referer: https://secure.payu.in/"
	-H "Content-Type: application/x-www-form-urlencoded")
return_url="http://waf:8080/api/method/hostel360.api.payments.payu_return"

for fixture in payu-return-success payu-return-failure; do
	load_form "${here}/${fixture}.txt"
	response="$(client "${edge}" "${payu[@]}" "${form[@]}" "${return_url}")"
	code="$(status_of "${response}")"
	if [[ "${code}" == 303 ]]; then
		pass "PayU return post passes the WAF (${fixture#payu-return-})"
	else
		fail "PayU return post passes the WAF (${fixture#payu-return-})" "status ${code} (303 from the application expected)"
	fi
done

load_form "${here}/payu-return-success.txt"
response="$(client "${edge}" -H "Host: ${site}" -A "PayU-Webhook/1.0" -H "Content-Type: application/x-www-form-urlencoded" \
	"${form[@]}" "http://waf:8080/api/method/hostel360.api.payments.payu_webhook")"
code="$(status_of "${response}")"
if [[ "${code}" == 200 ]]; then
	pass "PayU webhook post passes the WAF"
else
	fail "PayU webhook post passes the WAF" "status ${code}"
fi

# The PayU exemption lifts attack patterns in argument values only: the protocol rules and
# request headers are still enforced.
response="$(client "${edge}" "${payu[@]}" --data "status=success&field9=declined%00injected" "${return_url}")"
code="$(status_of "${response}")"
if [[ "${code}" == 403 ]]; then
	pass "null byte in a PayU field is still blocked"
else
	fail "null byte in a PayU field is still blocked" "status ${code}"
fi

response="$(client "${edge}" "${payu[@]}" -A "sqlmap/1.8.10#stable (https://sqlmap.org)" \
	--data-urlencode "status=success" "${return_url}")"
code="$(status_of "${response}")"
if [[ "${code}" == 403 ]]; then
	pass "scanner user agent on the PayU return path is still blocked"
else
	fail "scanner user agent on the PayU return path is still blocked" "status ${code}"
fi

response="$(client "${edge}" "${lb[@]}" -H "Content-Type: application/x-www-form-urlencoded" \
	--data-urlencode "mobile=9876543210' UNION SELECT name, password FROM \`__Auth\`-- -" \
	"http://waf:8080/api/method/hostel360.api.auth.send_otp")"
code="$(status_of "${response}")"
if [[ "${code}" == 403 ]]; then
	pass "SQL injection on the sign-in endpoint is still blocked"
else
	fail "SQL injection on the sign-in endpoint is still blocked" "status ${code}"
fi

response="$(client "${edge}" "${lb[@]}" -H "Content-Type: application/x-www-form-urlencoded" \
	--data-urlencode "txnid=<script>alert(document.cookie)</script>" \
	"http://waf:8080/api/method/hostel360.api.payments.payu_return_status")"
code="$(status_of "${response}")"
if [[ "${code}" == 403 ]]; then
	pass "the PayU exemption is limited to its two exact paths"
else
	fail "the PayU exemption is limited to its two exact paths" "status ${code}"
fi

# Desk requests the CRS used to take for attacks; each one showed staff a "Not permitted"
# message (number cards on every workspace, user settings on every page) or cut realtime off.
form_post=("${lb[@]}" -H "Content-Type: application/x-www-form-urlencoded")
card='{"name":"Open Grievances","doctype":"Number Card","type":"Document Type","document_type":"Hostel Grievance","function":"Count","filters_json":"[[\"Hostel Grievance\",\"status\",\"=\",\"Open\"]]","dynamic_filters_json":"[]"}'
response="$(client "${edge}" "${form_post[@]}" --data-urlencode "doc=${card}" --data-urlencode "filters=[]" \
	"http://waf:8080/api/method/frappe.desk.doctype.number_card.number_card.get_result")"
code="$(status_of "${response}")"
if [[ "${code}" == 200 ]]; then
	pass "desk number cards pass the WAF"
else
	fail "desk number cards pass the WAF" "status ${code}"
fi
response="$(client "${edge}" "${form_post[@]}" --data-urlencode "doc=${card}" \
	--data-urlencode "to_date=2026-10-16' UNION SELECT name, password FROM \`__Auth\`-- -" \
	"http://waf:8080/api/method/frappe.desk.doctype.number_card.number_card.get_result")"
code="$(status_of "${response}")"
if [[ "${code}" == 403 ]]; then
	pass "SQL injection in other number card arguments is still blocked"
else
	fail "SQL injection in other number card arguments is still blocked" "status ${code}"
fi

settings='{"updated_on":"Fri Oct 16 2026 09:17:08 GMT+0530","last_view":"List","List":{"filters":[],"sort_by":"modified","sort_order":"desc"}}'
response="$(client "${edge}" "${form_post[@]}" --data-urlencode "doctype=Hostel Student" \
	--data-urlencode "user_settings=${settings}" "http://waf:8080/api/method/frappe.model.utils.user_settings.save")"
code="$(status_of "${response}")"
if [[ "${code}" == 200 ]]; then
	pass "desk user settings (a method name ending in .save) pass the WAF"
else
	fail "desk user settings (a method name ending in .save) pass the WAF" "status ${code}"
fi
response="$(client "${edge}" "${lb[@]}" "http://waf:8080/files/site-backup.sql")"
code="$(status_of "${response}")"
if [[ "${code}" == 403 ]]; then
	pass "restricted file extensions are still blocked outside method names"
else
	fail "restricted file extensions are still blocked outside method names" "status ${code}"
fi

response="$(client "${edge}" "${lb[@]}" -H "Content-Type: text/plain;charset=UTF-8" --data '40{"sid":"x"}' \
	"http://waf:8080/socket.io/?EIO=4&transport=polling&t=Q52u18f&sid=abc")"
code="$(status_of "${response}")"
if [[ "${code}" == 200 ]]; then
	pass "realtime long polling (text/plain) passes the WAF"
else
	fail "realtime long polling (text/plain) passes the WAF" "status ${code}"
fi
response="$(client "${edge}" "${lb[@]}" -H "Content-Type: text/plain" --data "x=1" \
	"http://waf:8080/api/method/hostel360.api.portal.get_dashboard")"
code="$(status_of "${response}")"
if [[ "${code}" == 403 ]]; then
	pass "text/plain bodies are still refused outside realtime"
else
	fail "text/plain bodies are still refused outside realtime" "status ${code}"
fi

# In-stack clients: the portal host name resolves to the WAF on the internal network.
pdf_agent="Mozilla/5.0 (Unknown; Linux aarch64) AppleWebKit/602.1 (KHTML, like Gecko) wkhtmltopdf Version/10.0 Safari/602.1"
response="$(client "${internal}" -A "${pdf_agent}" "https://${site}/files/institution-logo.png")"
code="$(status_of "${response}")"
if [[ "${code}" == 200 ]]; then
	pass "stack containers reach https://${site} (443) at the WAF"
else
	fail "stack containers reach https://${site} (443) at the WAF" "status ${code}: $(tail -n 2 <<<"${response}" | head -n 1)"
fi
response="$(client "${internal}" -A "${pdf_agent}" "http://${site}/assets/frappe/images/frappe-favicon.svg")"
code="$(status_of "${response}")"
if [[ "${code}" == 200 ]]; then
	pass "stack containers reach http://${site} (80) at the WAF"
else
	fail "stack containers reach http://${site} (80) at the WAF" "status ${code}: $(tail -n 2 <<<"${response}" | head -n 1)"
fi

# The listeners added for the stack must not change what the load balancer can reach.
published="$(docker port "$(compose ps -q waf)" | sed 's/ -> .*//' | sort | tr '\n' ' ')"
if [[ "${published}" == "8080/tcp 8443/tcp " ]]; then
	pass "only 8080 and 8443 are published on the host"
else
	fail "only 8080 and 8443 are published on the host" "published: ${published}"
fi

response="$(client "${edge}" "${lb[@]}" -H "X-Forwarded-Proto: http" "http://waf:8080/hostel/fees?x=1")"
if [[ "$(status_of "${response}")" == 301 && "$(header_of "${response}" Location)" == "https://${site}/hostel/fees?x=1" ]]; then
	pass "visitors who came over plain HTTP are redirected to HTTPS"
else
	fail "visitors who came over plain HTTP are redirected to HTTPS" "status $(status_of "${response}"), Location: $(header_of "${response}" Location)"
fi
response="$(client "${edge}" "${lb[@]}" -H "X-Forwarded-Proto: https" "http://waf:8080/api/method/hostel360.api.health.liveness")"
if [[ "$(status_of "${response}")" == 200 ]]; then
	pass "HTTPS visitors and internal requests are served without a redirect"
else
	fail "HTTPS visitors and internal requests are served without a redirect" "status $(status_of "${response}")"
fi

response="$(client "${edge}" -H "Host: 203.0.113.10" "http://waf:8080/hostel")"
code="$(status_of "${response}")"
if [[ "${code}" == 421 ]]; then
	pass "unknown host names are still refused (421)"
else
	fail "unknown host names are still refused (421)" "status ${code}"
fi

echo
if [[ "${failures}" -eq 0 ]]; then
	echo "all WAF checks passed (${waf_image})"
else
	echo "${failures} WAF check(s) failed (${waf_image})"
	grep -E 'ModSecurity: Access denied' "${work}/data/logs/waf/error.log" 2>/dev/null |
		grep -oE '\[(id|msg|uri) "[^"]*"\]' | tail -n 15 || true
fi
exit "${failures}"
