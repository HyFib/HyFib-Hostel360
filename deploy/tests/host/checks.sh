#!/usr/bin/env bash
# Checks for the host scripts, run inside the tools image by deploy/tests/host/run.sh (as
# root, in a throwaway container: the scripts are installed where the server has them).
set -Eeuo pipefail

deploy=/work/deploy
stub=/tmp/h360-stub
port=18080
secret="test-secret-$(date +%s)"
failures=0
spool=/var/spool/hostel360/ingest

pass() { printf 'ok    %s\n' "$1"; }
fail() {
	printf 'FAIL  %s\n' "$1"
	[[ -n "${2:-}" ]] && printf '      %s\n' "$2"
	failures=$((failures + 1))
}
check() {
	# check <name> <command...>: passes when the command succeeds
	local name="$1"
	shift
	if "$@"; then pass "${name}"; else fail "${name}"; fi
}

# --------------------------------------------------------------------------- install

install -d /usr/local/lib/hostel360 /etc/hostel360 /var/spool/hostel360 /var/lib/hostel360 "${stub}"
install -m 0644 "${deploy}/host/bin/h360-lib.sh" /usr/local/lib/hostel360/h360-lib.sh
for script in "${deploy}"/host/bin/h360-*; do
	[[ "${script##*/}" == h360-lib.sh ]] && continue
	install -m 0755 "${script}" /usr/local/sbin/
done
cat >/etc/hostel360/agent.env <<EOF
H360_HOST_LABEL=test-host
H360_SITE_NAME=hostel.test
H360_PUBLIC_URL=http://127.0.0.1:${port}
H360_PROBE_URL=http://127.0.0.1:${port}/liveness
H360_INGEST_URL=http://127.0.0.1:${port}/ingest
H360_INGEST_SECRET=${secret}
H360_PROBE_BATCH=5
H360_PROBE_FAIL_ALERT=3
H360_PROBE_TIMEOUT=3
H360_INGEST_LOCK_WAIT=1
EOF
chmod 0600 /etc/hostel360/agent.env

python3 -I "${deploy}/tests/host/ingest_stub.py" "${port}" "${stub}" "${secret}" &
stub_pid=$!
trap 'kill "${stub_pid}" 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
	curl -s -o /dev/null "http://127.0.0.1:${port}/liveness" && break
	sleep 0.1
done

ingest_mode() { printf '%s' "$1" >"${stub}/ingest-mode"; }
portal_mode() { printf '%s' "$1" >"${stub}/portal-mode"; }
reset() {
	rm -rf "${spool}" /var/lib/hostel360/probe "${stub}/requests.jsonl"
	ingest_mode ok
	portal_mode 200
}
spooled() { find "${spool}" -maxdepth 1 -type f -name "${1:-*.json}" 2>/dev/null | wc -l; }
requests() { cat "${stub}/requests.jsonl" 2>/dev/null || true; }
send() {
	# send <json>: h360-ingest's exit status
	local status=0
	printf '%s' "$1" | h360-ingest - 2>/dev/null || status=$?
	printf '%s' "${status}"
}
uuid_re='^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'

# --------------------------------------------------------------------------- h360-ingest

reset
status="$(send '{"type": "heartbeat", "source": "Host Agent", "host": "test-host"}')"
id="$(requests | jq -r 'select(.committed) | .body.message_id' | head -n 1)"
if [[ "${status}" == 0 && "${id}" =~ ${uuid_re} ]]; then
	pass "a message gets a random message_id (uuid4) and is delivered"
else
	fail "a message gets a random message_id (uuid4) and is delivered" "exit ${status}, message_id '${id}'"
fi

reset
status="$(send '{"type": "heartbeat", "host": "test-host", "message_id": "agent:fixed-id-0001"}')"
check "a valid message_id given by the caller is kept" \
	test "$(requests | jq -r '.body.message_id' | head -n 1)" == "agent:fixed-id-0001"
status="$(send '{"type": "heartbeat", "host": "test-host", "message_id": "bad id!"}')"
check "an invalid message_id is refused before sending" test "${status}" == 1 -a "$(spooled)" == 0

reset
ingest_mode 503
status="$(send '{"type": "alert", "source": "Host Agent", "key": "test.down", "severity": "Warning", "title": "t"}')"
file="$(find "${spool}" -maxdepth 1 -name '*.json' | head -n 1)"
id="$(jq -r '.message_id // ""' "${file:-/dev/null}" 2>/dev/null || true)"
check "503: the message is spooled (exit 1) with its message_id" test "${status}" == 1 -a -n "${id}"
ingest_mode ok
h360-ingest --flush 2>/dev/null || true
check "the retry carries the same message_id and empties the spool" \
	test "$(requests | jq -r 'select(.committed) | .body.message_id')" == "${id}" -a "$(spooled)" == 0

reset
ingest_mode commit-500
status="$(send '{"type": "backup", "source": "Host Agent", "backup_type": "Daily", "status": "Success"}')"
ingest_mode ok
h360-ingest --flush 2>/dev/null || true
committed="$(requests | jq -s '[.[] | select(.committed)] | length')"
replayed="$(requests | jq -s '[.[] | select(.status == 409)] | length')"
if [[ "${status}" == 1 && "${committed}" == 1 && "${replayed}" == 1 && "$(spooled)" == 0 ]]; then
	pass "a retry of a message the server did commit is answered 409 and not recorded twice"
else
	fail "a retry of a message the server did commit is answered 409 and not recorded twice" \
		"exit ${status}, committed ${committed}, 409s ${replayed}, spooled $(spooled)"
fi

reset
ingest_mode 417
status="$(send '{"type": "heartbeat", "host": "test-host"}')"
check "417 is retried later (spooled), not dropped" test "${status}" == 1 -a "$(spooled)" == 1

for code in 400 413 422; do
	reset
	ingest_mode "${code}"
	status="$(send '{"type": "heartbeat", "host": "test-host"}')"
	check "${code} is dropped for good (exit 2, nothing spooled)" test "${status}" == 2 -a "$(spooled)" == 0
done

reset
mkdir -p "${spool}"
printf '{"type":"heartbeat","host":"old-agent"}' >"${spool}/20260101000000-oldmsg01.json"
h360-ingest --flush 2>/dev/null || true
id="$(requests | jq -r 'select(.body.host == "old-agent") | .body.message_id' | head -n 1)"
check "a message spooled by an older agent gets a message_id when it is retried" \
	bash -c "[[ '${id}' =~ ${uuid_re} ]]"

# Probe samples are the record of an outage: kept 35 days, outside the 24 h / count limits.
reset
ingest_mode 503
probe='{"type": "metrics", "source": "Uptime Probe", "host": "test-host", "samples": [{"metric": "uptime_probe", "value": 0}]}'
send "${probe}" >/dev/null
check "probe samples are spooled apart (.keep.json)" test "$(spooled '*.keep.json')" == 1
printf '{"type":"heartbeat","host":"x","message_id":"old-ordinary-1"}' >"${spool}/20260101000000-ordinary.json"
printf '{"type":"metrics","source":"Uptime Probe","samples":[],"message_id":"old-probe-0001"}' >"${spool}/20260101000000-probe.keep.json"
printf '{"type":"metrics","source":"Uptime Probe","samples":[],"message_id":"ancient-probe-1"}' >"${spool}/20250101000000-ancient.keep.json"
touch -d '-2 days' "${spool}/20260101000000-ordinary.json" "${spool}/20260101000000-probe.keep.json"
touch -d '-40 days' "${spool}/20250101000000-ancient.keep.json"
h360-ingest --flush 2>/dev/null || true
if [[ ! -e "${spool}/20260101000000-ordinary.json" && -e "${spool}/20260101000000-probe.keep.json" &&
	! -e "${spool}/20250101000000-ancient.keep.json" ]]; then
	pass "after 24 h ordinary messages are dropped, probe samples kept (until 35 days)"
else
	fail "after 24 h ordinary messages are dropped, probe samples kept (until 35 days)" "$(ls "${spool}")"
fi

reset
ingest_mode 503
mkdir -p "${spool}"
for i in 1 2 3; do
	printf '{"type":"metrics","source":"Uptime Probe","samples":[],"message_id":"probe-batch-%s"}' "${i}" \
		>"${spool}/2026010100000${i}-p${i}.keep.json"
	printf '{"type":"heartbeat","message_id":"ordinary-msg-%s"}' "${i}" >"${spool}/2026010100000${i}-o${i}.json"
done
printf '{"type": "heartbeat", "host": "test-host"}' | H360_SPOOL_MAX_FILES=3 h360-ingest - 2>/dev/null || true
check "a full spool drops ordinary messages, never the probe samples" \
	test "$(spooled '*.keep.json')" == 3 -a "$(spooled)" == 4

reset
ingest_mode ok
mkdir -p /run/lock
exec {lock}>/run/lock/h360-ingest.lock
flock "${lock}"
status="$(send '{"type": "heartbeat", "host": "busy"}')"
flock -u "${lock}"
exec {lock}>&-
check "while another run holds the lock the message is spooled, not lost" test "${status}" == 1 -a "$(spooled)" == 1
h360-ingest --flush 2>/dev/null || true
check "and goes out with the next flush" test "$(requests | jq -r 'select(.committed) | .body.host')" == busy

# --------------------------------------------------------------------------- h360-uptime-probe

reset
portal_mode 503
for _ in 1 2 3 4 5; do
	h360-uptime-probe 2>/dev/null || true
done
alerts="$(requests | jq -s '[.[] | select(.committed and .body.type == "alert" and .body.source == "Uptime Probe")] | length')"
check "an outage raises one alert, not one a minute (5 failed minutes, threshold 3)" test "${alerts}" == 1
touch -d '-61 minutes' /var/lib/hostel360/probe/alert_open
h360-uptime-probe 2>/dev/null || true
alerts="$(requests | jq -s '[.[] | select(.committed and .body.type == "alert")] | length')"
check "the alert is repeated once an hour while the outage lasts" test "${alerts}" == 2
samples="$(requests | jq -s '[.[] | select(.committed and .body.type == "metrics") | .body.samples[] | select(.metric == "uptime_probe")] | length')"
check "every failed minute is recorded as an uptime_probe sample" test "${samples}" -ge 1
portal_mode 200
h360-uptime-probe 2>/dev/null || true
resolves="$(requests | jq -s '[.[] | select(.committed and .body.type == "resolve")] | length')"
check "recovery resolves the alert once" test "${resolves}" == 1
downs="$(requests | jq -s '[.[] | select(.committed and .body.type == "metrics") | .body.samples[] | select(.metric == "uptime_probe" and .value == 0)] | length')"
check "all six down minutes reached the server" test "${downs}" == 6

reset
portal_mode 503
ingest_mode 503
for _ in 1 2 3 4 5 6; do
	h360-uptime-probe 2>/dev/null || true
done
check "while ingest is down, only the outage alert and probe batches are spooled" \
	test "$(spooled '*.keep.json')" -ge 1 -a "$(spooled)" -le 4

# --------------------------------------------------------------------------- entrypoint queues

work="$(mktemp -d)"
mkdir -p "${work}/sites" "${work}/env/bin"
ln -s "$(command -v python3)" "${work}/env/bin/python"
sed -n '/^worker_queues() {/,/^}/p' "${deploy}/docker/entrypoint.sh" >"${work}/queues.sh"
# shellcheck disable=SC2329 # run in a child bash through declare -f below
queues_check() {
	cd "${work}"
	# shellcheck disable=SC1091
	source ./queues.sh
	echo '{"workers": {"alerts": {"timeout": 300}}}' >sites/common_site_config.json
	[[ "$(worker_queues alerts,long,default,short 2>/dev/null)" == alerts,long,default,short ]] || return 1
	echo '{}' >sites/common_site_config.json
	[[ "$(worker_queues alerts,short,default 2>/dev/null)" == short,default ]]
}
check "workers listen on alerts once it is defined, and start without it when it is not" \
	bash -c "$(declare -f queues_check); work='${work}'; queues_check"
rm -rf "${work}"

# --------------------------------------------------------------------------- Kuma relay

if python3 -I "${deploy}/tests/host/test_kuma_relay.py" >/tmp/relay-tests.log 2>&1; then
	pass "Uptime Kuma relay: $(grep -E '^Ran ' /tmp/relay-tests.log)"
else
	fail "Uptime Kuma relay" "$(tail -n 20 /tmp/relay-tests.log)"
fi

printf '\n%s\n' "$([[ "${failures}" -eq 0 ]] && echo 'all host checks passed' || echo "${failures} host check(s) failed")"
exit "$((failures > 0))"
