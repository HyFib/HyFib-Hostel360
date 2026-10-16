#!/usr/bin/env bash
# Safety of the database dump and of h360-restore (run by deploy/tests/host/run.sh, as root
# in a throwaway container). Docker, restic, rclone, rsync and curl are small fakes that act
# on local files; the library and h360-restore are the real ones.
#
#   - a dump that fails or is cut off is never accepted, even when the caller runs
#     h360_dump_databases inside a condition (which suspends errexit)
#   - h360-restore reads and checks the snapshot before it stops the site or drops anything
#   - a database that cannot be loaded is replaced by the pre-restore copy, and the sites
#     directory is left alone
set -Eeuo pipefail

deploy=/work/deploy
site=hostel.test
data=/srv/hostel360
fakes=/tmp/fakes
failures=0

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

install -d /usr/local/lib/hostel360 /etc/hostel360/secrets /var/spool/hostel360/ingest /var/cache/restic \
	"${fakes}" /run/lock
install -m 0644 "${deploy}/host/bin/h360-lib.sh" /usr/local/lib/hostel360/h360-lib.sh
install -m 0755 "${deploy}/host/bin/h360-restore" "${deploy}/host/bin/h360-ingest" /usr/local/sbin/
printf '#!/bin/bash\nexit 0\n' >/usr/local/sbin/h360-backup
chmod 0755 /usr/local/sbin/h360-backup

cat >/etc/hostel360/agent.env <<'EOF'
H360_HOST_LABEL=test-host
H360_INGEST_URL=http://127.0.0.1:1/ingest
H360_INGEST_SECRET=x
EOF
cat >/etc/hostel360/backup.env <<'EOF'
RESTIC_REPOSITORY=rclone:oos:hostel360-backups-mumbai/restic
RESTIC_CACHE_DIR=/var/cache/restic
EOF
printf 'SITE_NAME=%s\nH360_DATA_DIR=%s\nWAF_HTTPS_PORT=8443\n' "${site}" "${data}" >/etc/hostel360/stack.env
chmod 0600 /etc/hostel360/*.env

setup_server() {
	rm -rf "${data}" "${fakes:?}"/*.log "${fakes}"/mode-*
	install -d "${data}/sites/${site}/private/files"
	echo '{"db_name": "_live", "db_password": "secret"}' >"${data}/sites/${site}/site_config.json"
	echo 'live upload' >"${data}/sites/${site}/private/files/live.txt"
	touch "${fakes}/calls.log" "${fakes}/db.log"
}

# docker: containers "backend" and "mariadb"; mariadb-dump prints a live dump (or stops half
# way when mode-dump-cut exists); the mariadb client appends its input to db.log and fails
# for input that carries LOAD_FAILS.
cat >"${fakes}/docker" <<'EOF'
#!/bin/bash
echo "docker $*" >>/tmp/fakes/calls.log
case "$1" in
	ps)
		case "$*" in
			*service=backend*) echo backend ;;
			*service=mariadb*) echo mariadb ;;
		esac ;;
	compose) ;;
	exec)
		shift
		[[ "$1" == -i ]] && shift
		container="$1"
		shift
		case "$*" in
			*mariadb-dump*)
				printf -- '-- MariaDB dump\nCREATE DATABASE `_live`;\nINSERT INTO t VALUES (1); -- SAFETY_MARKER\n'
				if [[ -f /tmp/fakes/mode-dump-cut ]]; then
					printf 'INSERT INTO t VALUES (2'
					exit 2
				fi
				printf -- '-- Dump completed on 2026-10-16  1:30:00\n' ;;
			*"exec mariadb"*)
				input="$(cat)"
				printf '%s\n%s\n' "$*" "${input}" >>/tmp/fakes/db.log
				[[ "${input}" != *LOAD_FAILS* ]] ;;
			*curl*) printf 200 ;;
			*) ;; # bench set-maintenance-mode and the like
		esac ;;
esac
EOF

# restic: one database and one file snapshot; "dump" behaves per mode-restic-*.
cat >"${fakes}/restic" <<'EOF'
#!/bin/bash
echo "restic $*" >>/tmp/fakes/calls.log
case "$1" in
	snapshots)
		case "$*" in
			*--latest*) printf '[{"short_id": "snap1", "time": "2026-10-15T01:30:00"}]\n' ;;
			*) printf '[{"short_id": "%s", "time": "2026-10-15T01:30:00", "hostname": "test-host", "tags": ["db"]}]\n' "${*: -1}" ;;
		esac ;;
	dump)
		printf -- '-- MariaDB dump\nCREATE DATABASE `_live`;\nINSERT INTO t VALUES (42); -- SNAPSHOT_MARKER\n'
		if [[ -f /tmp/fakes/mode-restic-read-error ]]; then
			printf 'INSERT INTO t VALUES (43'
			echo "Fatal: pack 5f2e... not found" >&2
			exit 1
		fi
		if [[ -f /tmp/fakes/mode-restic-truncated ]]; then
			printf 'INSERT INTO t VALUES (43'
			exit 0
		fi
		[[ -f /tmp/fakes/mode-restic-load-fails ]] && printf 'SELECT 1; -- LOAD_FAILS\n'
		printf -- '-- Dump completed on 2026-10-15  1:30:00\n' ;;
	restore)
		target=""
		while [[ $# -gt 0 ]]; do
			[[ "$1" == --target ]] && target="$2"
			shift
		done
		mkdir -p "${target}/srv/hostel360/sites/hostel.test/private/files"
		echo '{"db_name": "_live", "db_password": "secret"}' >"${target}/srv/hostel360/sites/hostel.test/site_config.json"
		echo 'restored upload' >"${target}/srv/hostel360/sites/hostel.test/private/files/restored.txt" ;;
esac
EOF

# rsync -a --delete --exclude /assets SRC/ DST/: replace DST's content with SRC's.
cat >"${fakes}/rsync" <<'EOF'
#!/bin/bash
echo "rsync $*" >>/tmp/fakes/calls.log
src="${*: -2:1}"
dst="${*: -1}"
find "${dst}" -mindepth 1 -maxdepth 1 ! -name assets -exec rm -rf {} +
cp -a "${src}." "${dst}"
EOF

# curl: the WAF answers readiness and the login page; the ingest API is unreachable.
cat >"${fakes}/curl" <<'EOF'
#!/bin/bash
case "$*" in
	*health.readiness*) printf '{"message": {"status": "ok"}}' ;;
	*/login*) printf '<html>login</html>' ;;
	*) exit 7 ;;
esac
EOF
printf '#!/bin/bash\nexit 0\n' >"${fakes}/rclone"
chmod 0755 "${fakes}"/*
for tool in docker restic rclone rsync curl; do ln -sf "${fakes}/${tool}" "/usr/local/sbin/${tool}"; done

restore() {
	H360_CONFIG_DIR=/etc/hostel360 H360_LOCK_DIR=/run/lock h360-restore restore --yes >/tmp/restore.log 2>&1
}

# --------------------------------------------------------------------------- the dump

export H360_DATA_DIR="${data}" H360_CONFIG_DIR=/etc/hostel360
# shellcheck source=deploy/host/bin/h360-lib.sh
source /usr/local/lib/hostel360/h360-lib.sh

setup_server
touch "${fakes}/mode-dump-cut"
# A condition, as the real callers use: errexit is suspended inside the function.
if h360_dump_databases /tmp/cut.sql.gz; then accepted=1; else accepted=0; fi
rm -f "${fakes}/mode-dump-cut"
check "a dump that fails half way is refused and removed, even inside a condition" \
	test "${accepted}" = 0 -a ! -e /tmp/cut.sql.gz

setup_server
dump_ok() { h360_dump_databases /tmp/good.sql.gz && h360_dump_is_complete /tmp/good.sql.gz; }
check "a complete dump is accepted" dump_ok

printf -- '-- MariaDB dump\nINSERT INTO t VALUES (1' | gzip >/tmp/truncated.sql.gz
printf 'not gzip' >/tmp/corrupt.sql.gz
: >/tmp/empty.sql.gz
all_rejected() {
	! h360_dump_is_complete /tmp/truncated.sql.gz && ! h360_dump_is_complete /tmp/corrupt.sql.gz &&
		! h360_dump_is_complete /tmp/empty.sql.gz
}
check "a valid archive around truncated SQL, a damaged archive and an empty file are all rejected" all_rejected

# --------------------------------------------------------------------------- restore: unreadable snapshot

untouched() {
	! grep -q "DROP DATABASE" "${fakes}/db.log" &&
		! grep -q "compose .* stop" "${fakes}/calls.log" &&
		[[ -f "${data}/sites/${site}/private/files/live.txt" ]] &&
		[[ -z "$(ls -A "${data}/restore-staging" 2>/dev/null)" ]]
}

for mode in read-error truncated; do
	setup_server
	touch "${fakes}/mode-restic-${mode}"
	status=0
	restore || status=$?
	if [[ "${status}" != 0 ]] && untouched && grep -q "nothing was changed" /tmp/restore.log; then
		pass "restore with an unreadable snapshot (${mode}) stops before the site is touched"
	else
		fail "restore with an unreadable snapshot (${mode}) stops before the site is touched" \
			"$(cat /tmp/restore.log; grep -E 'DROP|stop' "${fakes}/db.log" "${fakes}/calls.log")"
	fi
done

# --------------------------------------------------------------------------- restore: load fails

setup_server
touch "${fakes}/mode-restic-load-fails"
status=0
restore || status=$?
safety_reloaded=0
# The last load must be the pre-restore copy (SAFETY_MARKER), after the failed snapshot load.
if [[ "$(grep -o 'SAFETY_MARKER\|LOAD_FAILS' "${fakes}/db.log" | tail -n 1)" == SAFETY_MARKER ]]; then
	safety_reloaded=1
fi
if [[ "${status}" != 0 && "${safety_reloaded}" == 1 ]] &&
	[[ -f "${data}/sites/${site}/private/files/live.txt" && ! -f "${data}/sites/${site}/private/files/restored.txt" ]] &&
	grep -q "previous database is back" /tmp/restore.log; then
	pass "a snapshot that cannot be loaded is replaced by the pre-restore copy; files are left alone"
else
	fail "a snapshot that cannot be loaded is replaced by the pre-restore copy; files are left alone" \
		"$(cat /tmp/restore.log)"
fi

# --------------------------------------------------------------------------- restore: success

setup_server
status=0
restore || status=$?
if [[ "${status}" == 0 ]] && grep -q SNAPSHOT_MARKER "${fakes}/db.log" &&
	[[ -f "${data}/sites/${site}/private/files/restored.txt" && ! -f "${data}/sites/${site}/private/files/live.txt" ]] &&
	[[ -z "$(ls -A "${data}/restore-staging" 2>/dev/null)" ]]; then
	pass "a good snapshot is loaded, the sites directory synchronised and the staging folder removed"
else
	fail "a good snapshot is loaded, the sites directory synchronised and the staging folder removed" \
		"$(cat /tmp/restore.log)"
fi

if [[ "${failures}" -gt 0 ]]; then
	printf '\n%d check(s) failed\n' "${failures}"
	exit 1
fi
printf '\nall backup safety checks passed\n'
