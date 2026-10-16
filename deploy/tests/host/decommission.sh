#!/usr/bin/env bash
# h360-decommission against a stand-in server (run by deploy/tests/host/run.sh, as root in a
# throwaway container). Docker, rclone and systemd are replaced by small fakes that act on
# the files the real ones would; gpg, tar and the script itself are real.
set -Eeuo pipefail

deploy=/work/deploy
site=hostel.test
data=/srv/hostel360
remote=/tmp/remote
fakes=/tmp/fakes
failures=0
escrowed_key="escrowed-encryption-key-0123456789"

pass() { printf 'ok    %s\n' "$1"; }
fail() {
	printf 'FAIL  %s\n' "$1"
	[[ -n "${2:-}" ]] && printf '%s\n' "$2" | sed 's/^/      /'
	failures=$((failures + 1))
}
check() {
	local name="$1"
	shift
	if "$@"; then pass "${name}"; else fail "${name}"; fi
}

# --------------------------------------------------------------------------- the server

install -d /usr/local/lib/hostel360 /etc/hostel360/secrets /var/spool/hostel360/ingest /var/lib/hostel360/probe \
	/var/cache/restic /usr/local/maldetect/quarantine /usr/local/maldetect/logs "${fakes}" "${remote}" /run/lock
install -m 0644 "${deploy}/host/bin/h360-lib.sh" /usr/local/lib/hostel360/h360-lib.sh
install -m 0755 "${deploy}/host/bin/h360-decommission" "${deploy}/host/bin/h360-ingest" /usr/local/sbin/

setup_server() {
	rm -rf "${data}" "${remote:?}"/* /root/h360-decommission-* "${fakes}/fail-drop-once" "${fakes}/down" "${fakes}/dropped"
	install -d "${data}/sites/${site}/private/files" "${data}/sites/hostel360-exit/certificate" \
		"${data}/mariadb" "${data}/redis-queue" "${data}/backups/pre-deploy" "${data}/logs/waf" \
		"${data}/logs/frappe" "${data}/clamav" "${data}/restore-test/x" /etc/hostel360/secrets
	echo '{"db_name": "_abc", "encryption_key": "'"${escrowed_key}"'"}' >"${data}/sites/${site}/site_config.json"
	echo '{}' >"${data}/sites/common_site_config.json"
	echo 'student photo' >"${data}/sites/${site}/private/files/photo.jpg"
	echo 'binlog with personal data' >"${data}/mariadb/mariadb-bin.000001"
	echo 'aof' >"${data}/redis-queue/appendonly.aof"
	echo 'dump' >"${data}/backups/pre-deploy/20261001-1.0.0.sql.gz"
	echo '10.20.1.5 - - [15/Oct/2026:10:00:00 +0530] "GET /hostel HTTP/1.1" 200' >"${data}/logs/waf/access.log"
	echo 'frappe web log line' >"${data}/logs/frappe/web.log"
	echo 'auth log line' >/var/log/auth.log
	echo 'quarantined upload' >/usr/local/maldetect/quarantine/upload.1234
	echo 'maldet event' >/usr/local/maldetect/logs/event_log
	echo 'spooled' >/var/spool/hostel360/ingest/x.json
	echo 'cache' >/var/cache/restic/x
	cat >/etc/hostel360/agent.env <<'EOF'
H360_HOST_LABEL=test-host
H360_INGEST_URL=http://127.0.0.1:1/ingest
H360_INGEST_SECRET=x
EOF
	cat >/etc/hostel360/backup.env <<'EOF'
RESTIC_REPOSITORY=rclone:oos:hostel360-backups-mumbai/restic
RESTIC_CACHE_DIR=/var/cache/restic
RCLONE_CONFIG_OOS_NAMESPACE=nsx
H360_BACKUP_EXTRA_PATHS="/var/log/audit /var/log/auth.log"
EOF
	printf 'SITE_NAME=%s\nH360_DATA_DIR=%s\n' "${site}" "${data}" >/etc/hostel360/stack.env
	printf 'root-password' >/etc/hostel360/secrets/db_root_password
	chmod 0600 /etc/hostel360/*.env
	(umask 077 && printf 'log archive passphrase 123' >/root/logs.pass && cp /root/logs.pass /root/logs.pass.copy)
	mkdir -p "${remote}/hostel360-backups-mumbai-exit-hold"
	python3 - "${escrowed_key}" >/root/cert.json <<'PY'
import hashlib, hmac, json, sys
key = hmac.new(sys.argv[1].encode(), b"hostel360/deletion-certificate/v1", hashlib.sha256).digest()
print(json.dumps({
	"certificate": {"certificate_id": "H360-DEL-hostel.test-20261015", "site": "hostel.test",
		"acknowledgement_reference": "ACGCET/HOSTEL/2027/014", "verification": {"clean": True}},
	"signature": {"algorithm": "HMAC-SHA256", "key_fingerprint": hashlib.sha256(b"fingerprint:" + key).hexdigest()[:16], "value": "ab"},
}))
PY
}

# Fakes: docker (containers "backend" and "mariadb"), rclone (a local folder), systemctl.
cat >"${fakes}/docker" <<'EOF'
#!/bin/bash
echo "docker $*" >>/tmp/fakes/calls.log
data=/srv/hostel360
case "$1" in
	ps)
		case "$*" in
			*service=backend*) [[ -f /tmp/fakes/down ]] || echo backend ;;
			*service=mariadb*) [[ -f /tmp/fakes/down ]] || echo mariadb ;;
			*) [[ -f /tmp/fakes/down ]] || printf 'backend\nmariadb\n' ;;
		esac ;;
	logs) echo "2026-10-15T10:00:00Z $2 container log line" ;;
	inspect) echo "/hostel360-${4}-1" ;;
	compose)
		case "$*" in *" down "*) touch /tmp/fakes/down ;; esac ;;
	exec)
		shift
		[[ "$1" == -i ]] && shift
		container="$1"
		shift
		case "$*" in
			*"cat > /tmp/h360-exit-certificate.json"*) cat >/dev/null ;;
			*h360-verify-deletion-certificate*) [[ -f "${data}/sites/hostel.test/site_config.json" ]] ;;
			*drop-site*)
				read -r password
				[[ "${password}" == root-password ]] || exit 3
				if [[ -f /tmp/fakes/fail-drop-once ]]; then rm -f /tmp/fakes/fail-drop-once; exit 1; fi
				mkdir -p "${data}/sites/.h360-dropped" && mv "${data}/sites/hostel.test" "${data}/sites/.h360-dropped/"
				touch /tmp/fakes/dropped ;;
			*"SHOW DATABASES"*) printf 'information_schema\nmysql\nperformance_schema\nsys\n' ;;
			*) ;; # bench set-maintenance-mode and the like
		esac ;;
esac
EOF
cat >"${fakes}/rclone" <<'EOF'
#!/bin/bash
echo "rclone $*" >>/tmp/fakes/calls.log
args=()
skip=0
for arg in "$@"; do
	if [[ "${skip}" == 1 ]]; then skip=0; continue; fi
	case "${arg}" in
		--max-depth | --exclude) skip=1 ;;
		--*) ;;
		*) args+=("${arg}") ;;
	esac
done
local_path() { printf '/tmp/remote/%s' "${1#oos:}"; }
case "${args[0]}" in
	lsf) [[ -d "$(local_path "${args[1]}")" ]] ;;
	copy) mkdir -p "$(local_path "${args[2]}")" && find "${args[1]}" -maxdepth 1 -type f ! -name '.*' -exec cp {} "$(local_path "${args[2]}")/" \; ;;
	check) for f in "${args[1]}"/*; do cmp -s "$f" "$(local_path "${args[2]}")/${f##*/}" || exit 1; done ;;
	copyto) cp "${args[1]}" "$(local_path "${args[2]}")" ;;
esac
EOF
printf '#!/bin/bash\necho "systemctl $*" >>/tmp/fakes/calls.log\n' >"${fakes}/systemctl"
printf '#!/bin/bash\necho "journal line"\n' >"${fakes}/journalctl"
chmod 0755 "${fakes}"/*
# The script puts /usr/local/sbin first on PATH; the fakes go there too.
for tool in docker rclone systemctl journalctl; do ln -sf "${fakes}/${tool}" "/usr/local/sbin/${tool}"; done

run() {
	# run <answers> <args...>: h360-decommission on a terminal, answering each prompt in turn
	local answers="$1"
	shift
	printf '%s\n' "${answers}" >/tmp/answers
	python3 -I "${deploy}/tests/answer_prompts.py" /tmp/answers /tmp/run.log h360-decommission "$@"
}

args=(--site "${site}" --certificate /root/cert.json --log-passphrase-file /root/logs.pass)
answers_ok="${escrowed_key}
${site}
ACGCET/HOSTEL/2027/014
DESTROY"

# --------------------------------------------------------------------------- plan

setup_server
status=0
h360-decommission "${args[@]}" >/tmp/plan.log 2>&1 || status=$?
if [[ "${status}" == 0 && -f "${data}/sites/${site}/site_config.json" ]] && grep -q 'Nothing was changed' /tmp/plan.log &&
	grep -q "${data}/mariadb" /tmp/plan.log; then
	pass "without --execute only the plan is printed"
else
	fail "without --execute only the plan is printed" "$(cat /tmp/plan.log)"
fi

status=0
h360-decommission "${args[@]}" --execute </dev/null >/tmp/plan.log 2>&1 || status=$?
check "--execute refuses to run without a terminal" test "${status}" != 0 -a -f "${data}/sites/${site}/site_config.json"

status=0
run "wrong-key" "${args[@]}" --execute || status=$?
check "a key that does not match the certificate stops everything" \
	test "${status}" != 0 -a -f "${data}/sites/${site}/site_config.json" -a ! -e /tmp/fakes/dropped
grep -q 'not the key the certificate was signed with' /tmp/run.log || fail "the wrong key is named as the reason" "$(cat /tmp/run.log)"

status=0
run "${escrowed_key}
${site}
ACGCET/HOSTEL/2027/014
destroy" "${args[@]}" --execute || status=$?
check "anything but DESTROY stops before a change" test "${status}" != 0 -a -f "${data}/sites/${site}/site_config.json"

rmdir "${remote}/hostel360-backups-mumbai-exit-hold"
status=0
run "${answers_ok}" "${args[@]}" --execute || status=$?
check "without the hold bucket (prepare-hold) nothing is done" test "${status}" != 0 -a -f "${data}/sites/${site}/site_config.json"
mkdir -p "${remote}/hostel360-backups-mumbai-exit-hold"

# --------------------------------------------------------------------------- an interrupted run, then the rest

touch /tmp/fakes/fail-drop-once
status=0
run "${answers_ok}" "${args[@]}" --execute || status=$?
hold="$(find "${remote}/hostel360-backups-mumbai-exit-hold" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
check "a failed drop stops the run after the log hold, with nothing deleted" \
	test "${status}" != 0 -a -f "${hold}/manifest.json" -a -f "${data}/mariadb/mariadb-bin.000001"
check "the failed run leaves its record" bash -c 'jq -e ".status == \"failed\"" /root/h360-decommission-*.json >/dev/null'

status=0
run "${answers_ok}" "${args[@]}" --execute || status=$?
check "the same command again finishes the work" test "${status}" == 0
check "the restart did not take the log hold again" test "$(find "${hold}" -name 'logs-*.tar.gz.gpg' ! -name '*-final*' | wc -l)" == 1

leftover=()
for path in "${data}/sites" "${data}/mariadb" "${data}/redis-queue" "${data}/backups" "${data}/logs" \
	"${data}/clamav" "${data}/restore-test" "${data}/exit-log-hold" /var/cache/restic /var/spool/hostel360 \
	/var/lib/hostel360 /etc/hostel360 /root/logs.pass; do
	[[ -e "${path}" ]] && leftover+=("${path}")
done
check "every local store and secret is gone" test "${#leftover[@]}" == 0
[[ "${#leftover[@]}" == 0 ]] || printf '      left: %s\n' "${leftover[*]}"
check "the malware quarantine is emptied" test -d /usr/local/maldetect/quarantine -a -z "$(ls -A /usr/local/maldetect/quarantine)"
check "the site was dropped through bench drop-site" test -f /tmp/fakes/dropped
check "the containers were removed and the stack disabled" \
	bash -c 'grep -q "compose .* down" /tmp/fakes/calls.log && grep -q "systemctl disable --now hostel360-stack.service" /tmp/fakes/calls.log'
check "the timers were stopped and disabled" grep -q 'systemctl disable --now h360-backup.timer' /tmp/fakes/calls.log

archive="$(find "${hold}" -name 'logs-*[0-9].tar.gz.gpg' | head -n 1)"
if gpg --batch --quiet --pinentry-mode loopback --passphrase-file /root/logs.pass.copy --decrypt "${archive}" 2>/dev/null |
	tar -xzO 2>/dev/null | grep -q 'GET /hostel'; then
	pass "the log hold holds the WAF access log, readable with the passphrase"
else
	fail "the log hold holds the WAF access log, readable with the passphrase"
fi
if gpg --batch --quiet --pinentry-mode loopback --passphrase-file /root/logs.pass.copy --decrypt "${archive}" 2>/dev/null |
	tar -tz 2>/dev/null | grep -q 'auth.log'; then
	pass "the host security logs are in it too"
else
	fail "the host security logs are in it too"
fi
check "a second archive and manifest hold what was logged after it" \
	test -f "${hold}/manifest-final.json" -a "$(find "${hold}" -name '*-final.tar.gz.gpg' | wc -l)" == 1
check "the certificate is kept with the logs" test -f "${hold}/H360-DEL-hostel.test-20261015.json"
check "the decommission record is uploaded as completed, every step listed" \
	bash -c "jq -e '.status == \"completed\" and ([.steps[].name] | index(\"dropped\") != null and index(\"deleted\") != null)' '${hold}/decommission-record.json' >/dev/null"
check "no passphrase or key is in the uploaded files" \
	bash -c "! grep -rqF -e 'log archive passphrase' -e '${escrowed_key}' -e root-password '${hold}'"

printf '\n%s\n' "$([[ "${failures}" -eq 0 ]] && echo 'all decommission checks passed' || echo "${failures} decommission check(s) failed")"
exit "$((failures > 0))"
