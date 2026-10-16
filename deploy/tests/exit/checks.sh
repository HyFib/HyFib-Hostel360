#!/usr/bin/env bash
# deploy/terraform/oci/exit-purge.sh against fake oci and terraform commands that keep the
# state of a small tenancy in files (run by deploy/tests/exit/run.sh). Checks the order of
# the destructive steps, the confirmations, the log hold and its date, and the records.
set -Eeuo pipefail

deploy="${H360_DEPLOY_DIR:-/work/deploy}"
# The shell exit-purge.sh runs under (administrators' workstations may have macOS's bash 3.2)
shell="${H360_EXIT_BASH:-bash}"
sim="$(mktemp -d)"
trap 'rm -rf -- "${sim}"' EXIT
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

mkdir -p "${sim}/bin" "${sim}/tf" "${sim}/home" "${sim}/state"
cp "${deploy}/terraform/oci/exit-purge.sh" "${sim}/tf/"
export SIM="${sim}/state" HOME="${sim}/home" PATH="${sim}/bin:${PATH}"

cat >"${sim}/bin/terraform" <<'EOF'
#!/usr/bin/env bash
echo "terraform $*" >>"${SIM}/calls.log"
case "$1" in
	output)
		[[ -f "${SIM}/destroyed" ]] && { echo "Output not found" >&2; exit 1; }
		cat <<'J'
{"name": "hostel360-prod", "compartment_id": "ocid1.compartment.x", "app_region": "ap-hyderabad-1",
 "backup_region": "ap-mumbai-1", "home_region": "ap-hyderabad-1", "namespace": "nsx",
 "backup_bucket": "hostel360-backups-mumbai", "instance_id": "ocid1.instance.x",
 "data_volume_id": "ocid1.volume.data", "boot_volume_id": "ocid1.bootvolume.boot",
 "dynamic_group_name": "hostel360-prod-app-servers", "log_group_id": "ocid1.loggroup.x",
 "log_ids": ["ocid1.log.a", "ocid1.log.b"], "log_retention_days": 180, "vault_id": "ocid1.vault.x",
 "key_id": "ocid1.key.x", "key_management_endpoint": "https://kms.example"}
J
		;;
	state)
		case "$2" in
			list)
				[[ -f "${SIM}/destroyed" ]] && exit 0
				printf '%s\n' data.oci_core_services.all oci_core_instance.app oci_core_volume.data \
					oci_logging_log_group.main 'oci_logging_log.lb["access"]' 'oci_kms_key.volumes[0]' 'oci_kms_vault.main[0]'
				;;
			rm) echo "$3" >>"${SIM}/state-rm" ;;
		esac
		;;
	destroy)
		printf 'Enter a value: '
		read -r answer
		[[ -f exit_override.tf ]] && grep -q 'prevent_destroy = false' exit_override.tf && touch "${SIM}/override-used"
		[[ -f "${SIM}/emptied" ]] || touch "${SIM}/destroyed-before-empty"
		[[ "${answer}" == yes ]] && touch "${SIM}/destroyed"
		;;
esac
exit 0
EOF
cat >"${sim}/bin/oci" <<'EOF'
#!/usr/bin/env bash
echo "oci $*" >>"${SIM}/calls.log"
args="$*"
case "${args}" in
	*"bucket get --bucket-name hostel360-backups-mumbai-exit-hold "*) [[ -f "${SIM}/hold" ]] || exit 1 ;;
	*"bucket get --bucket-name hostel360-backups-mumbai "*) [[ -f "${SIM}/destroyed" ]] && exit 1 ;;
	*"bucket create"*) touch "${SIM}/hold" ;;
	*"list-object-versions --bucket-name hostel360-backups-mumbai "*)
		if [[ -f "${SIM}/emptied" ]]; then echo '{"data": []}'; else echo '{"data": [{"name": "restic/config"}, {"name": "restic/data/0a"}]}'; fi ;;
	*"list-object-versions"*) echo '{"data": [{"name": "run/manifest.json"}]}' ;;
	*"multipart list"*) echo '{"data": []}' ;;
	*"bulk-delete-versions"*) touch "${SIM}/emptied" ;;
	*"bv backup list"*)
		if [[ -f "${SIM}/backups-deleted" ]]; then echo '{"data": []}'; else
			echo '{"data": [{"id": "ocid1.volumebackup.adhoc", "display-name": "hostel360-pre-deploy-20261001-101500", "volume-id": "ocid1.volume.data", "lifecycle-state": "AVAILABLE", "freeform-tags": {"created-by": "h360-snapshot"}},
			                {"id": "ocid1.volumebackup.other", "display-name": "another-system", "volume-id": "ocid1.volume.other", "lifecycle-state": "AVAILABLE", "freeform-tags": {}}]}'
		fi ;;
	*"bv boot-volume-backup list"*)
		if [[ -f "${SIM}/backups-deleted" ]]; then echo '{"data": []}'; else
			echo '{"data": [{"id": "ocid1.bootvolumebackup.daily", "display-name": "hostel360-prod-app (Boot Volume)", "boot-volume-id": "ocid1.bootvolume.boot", "lifecycle-state": "AVAILABLE"}]}'
		fi ;;
	*"bv backup delete"* | *"bv boot-volume-backup delete"*)
		[[ -f "${SIM}/destroyed" ]] || touch "${SIM}/backup-deleted-before-destroy"
		[[ "${args}" == *volumebackup.other* ]] && touch "${SIM}/foreign-backup-deleted"
		echo x >>"${SIM}/backup-deletes"
		[[ "$(wc -l <"${SIM}/backup-deletes")" -ge 4 ]] && touch "${SIM}/backups-deleted" ;;
	*"retention-rule list"*) if [[ -f "${SIM}/rule" ]]; then echo '{"data": {"items": [{"id": "rule1"}]}}'; else echo '{"data": {"items": []}}'; fi ;;
	*"retention-rule create"*) echo "${args}" >"${SIM}/rule" ;;
	*"retention-rule delete"*) touch "${SIM}/rule-deleted" ;;
	*"iam policy list"*) touch "${SIM}/policies"; jq -Rn '{data: [inputs | select(length > 0) | {name: ., id: ("id-" + .)}]}' <"${SIM}/policies" ;;
	*"iam policy create"*)
		name="$(sed 's/.*--name \([^ ]*\).*/\1/' <<<"${args}")"
		echo "${name}" >>"${SIM}/policies"
		echo "${args}" >>"${SIM}/policy-statements" ;;
	*"iam policy delete"*)
		name="$(sed 's/.*--policy-id id-\([^ ]*\).*/\1/' <<<"${args}")"
		grep -vx "${name}" "${SIM}/policies" >"${SIM}/policies.new" || true
		mv "${SIM}/policies.new" "${SIM}/policies" ;;
	*"object-lifecycle-policy put"*) echo "${args}" >"${SIM}/lifecycle" ;;
	*"os object list --bucket-name hostel360-backups-mumbai-exit-hold"*)
		[[ -f "${SIM}/decommissioned" ]] && echo '{"data": [{"name": "run/manifest.json"}, {"name": "run/decommission-record.json"}]}' || echo '{"data": []}' ;;
	*"os object get"*) echo '{"status": "completed", "certificate_id": "H360-DEL-hostel.test-20261015", "finished_at": "2026-10-15T10:00:00+0530"}' ;;
	*"kms management key schedule-deletion"*) [[ -f "${SIM}/backups-deleted" ]] || touch "${SIM}/key-before-backups"; touch "${SIM}/key-scheduled" ;;
	*"kms management vault schedule-deletion"*) [[ -f "${SIM}/key-scheduled" ]] || touch "${SIM}/vault-before-key"; touch "${SIM}/vault-scheduled" ;;
	*"os object bulk-delete "*) touch "${SIM}/hold-emptied" ;;
	*"os bucket delete"*) rm -f "${SIM}/hold" ;;
	*"logging log delete"*) echo x >>"${SIM}/logs-deleted" ;;
	*"logging log-group delete"*) touch "${SIM}/log-group-deleted" ;;
	*"iam user get"*) echo admin.user ;;
esac
exit 0
EOF
chmod 0755 "${sim}/bin/"*
cat >"${sim}/cert.json" <<'EOF'
{"certificate": {"certificate_id": "H360-DEL-hostel.test-20261015", "site": "hostel.test",
 "acknowledgement_reference": "ACGCET/HOSTEL/2027/014", "verification": {"clean": true}},
 "signature": {"value": "ab", "key_fingerprint": "00"}}
EOF

purge() {
	# purge <answers> <args...>
	printf '%s\n' "$1" >"${sim}/answers"
	shift
	(cd "${sim}/tf" && python3 -I "${deploy}/tests/answer_prompts.py" "${sim}/answers" "${sim}/out.log" "${shell}" ./exit-purge.sh "$@")
}
records="${HOME}/hostel360-exit"

# --------------------------------------------------------------------------- plan

status=0
(cd "${sim}/tf" && "${shell}" ./exit-purge.sh plan >"${sim}/out.log" 2>&1) || status=$?
if [[ "${status}" == 0 ]] && grep -q '2 object versions' "${sim}/out.log" &&
	grep -q 'NOT deleted' "${sim}/out.log" && grep -q 'another-system' "${sim}/out.log" &&
	! grep -qE 'bulk-delete|delete|destroy|create' "${SIM}/calls.log"; then
	pass "plan lists the backups (and a foreign one as not touched) and changes nothing"
else
	fail "plan lists the backups (and a foreign one as not touched) and changes nothing" "$(cat "${sim}/out.log")"
fi

status=0
purge "" execute --certificate "${sim}/cert.json" || status=$?
check "execute refuses to start before a log hold is prepared" \
	bash -c "[[ ${status} != 0 ]] && grep -q 'no log hold' '${sim}/out.log' && [[ ! -f '${SIM}/emptied' ]]"

# --------------------------------------------------------------------------- prepare-hold

status=0
purge "wrong-name" prepare-hold || status=$?
check "prepare-hold asks for the backup bucket's name first" test "${status}" != 0 -a ! -f "${SIM}/hold"
status=0
purge "hostel360-backups-mumbai" prepare-hold || status=$?
check "prepare-hold makes the hold bucket" test "${status}" == 0 -a -f "${SIM}/hold"
check "with a 181-day retention rule" grep -q -- '--time-amount 181 --time-unit DAYS' "${SIM}/rule"
check "and a lifecycle rule a day later" grep -q '"timeAmount":182' "${SIM}/lifecycle"
check "the server may add objects to it, never delete or change them" \
	bash -c "grep -q 'OBJECT_CREATE' '${SIM}/policy-statements' && ! grep -qE 'OBJECT_DELETE|OBJECT_OVERWRITE' '${SIM}/policy-statements'"
until_date="$(jq -r .until "${records}/hold.json")"
expected="$(jq -rn 'now + 181 * 86400 | strftime("%Y-%m-%d")')"
check "the hold date is recorded, 181 days on" test "${until_date}" == "${expected}"

# --------------------------------------------------------------------------- execute

status=0
purge "" execute --certificate "${sim}/cert.json" || status=$?
check "execute waits for the server's decommission record" \
	bash -c "[[ ${status} != 0 ]] && grep -q 'no decommission record' '${sim}/out.log' && [[ ! -f '${SIM}/emptied' ]]"

touch "${SIM}/decommissioned"
status=0
purge "hostel360-backups-mumbai
ACGCET/HOSTEL/2027/015" execute --certificate "${sim}/cert.json" || status=$?
check "a wrong acknowledgement reference stops it before any deletion" test "${status}" != 0 -a ! -f "${SIM}/emptied"

status=0
purge "hostel360-backups-mumbai
ACGCET/HOSTEL/2027/014
DESTROY hostel360-prod
yes" execute --certificate "${sim}/cert.json" || status=$?
check "execute runs through with every confirmation given" test "${status}" == 0
check "the server's access to the hold bucket goes first" bash -c "! grep -qx hostel360-prod-exit-hold-server '${SIM}/policies'"
check "every object version of the backup bucket is deleted before the destroy" \
	test -f "${SIM}/emptied" -a ! -f "${SIM}/destroyed-before-empty"
check "the destroy lifts prevent_destroy through the override, which is removed afterwards" \
	test -f "${SIM}/override-used" -a ! -f "${sim}/tf/exit_override.tf"
check "the CERT-In logs and the Vault key are kept out of the destroy" \
	bash -c "grep -q oci_logging_log_group.main '${SIM}/state-rm' && grep -q 'oci_kms_key.volumes' '${SIM}/state-rm' && ! grep -q oci_core_volume '${SIM}/state-rm'"
check "volume and boot-volume backups are deleted after the destroy, in both regions" \
	bash -c "[[ -f '${SIM}/backups-deleted' && ! -f '${SIM}/backup-deleted-before-destroy' ]] && grep -q 'boot-volume-backup delete.*ap-mumbai-1' '${SIM}/calls.log'"
check "a backup that is not the server's is left alone" test ! -f "${SIM}/foreign-backup-deleted"
check "the Vault key is scheduled for deletion after the backups, then the vault" \
	test -f "${SIM}/vault-scheduled" -a ! -f "${SIM}/key-before-backups" -a ! -f "${SIM}/vault-before-key"
check "every action is recorded with the operator" \
	bash -c "jq -e -s 'any(.[]; .action == \"backup bucket emptied\") and all(.[]; .operator == \"admin.user\")' '${records}/actions.jsonl' >/dev/null"

# --------------------------------------------------------------------------- release

status=0
purge "RELEASE hostel360-prod" release || status=$?
check "release is refused before the hold date" \
	bash -c "[[ ${status} != 0 ]] && grep -q 'may not be released before' '${sim}/out.log' && [[ -f '${SIM}/hold' ]]"
jq '.until = "2026-01-01"' "${records}/hold.json" >"${records}/hold.json.new" && mv "${records}/hold.json.new" "${records}/hold.json"
status=0
purge "RELEASE hostel360-prod" release || status=$?
check "after the hold date the hold bucket, its rule and policy go" \
	bash -c "[[ ${status} == 0 && ! -f '${SIM}/hold' && -f '${SIM}/rule-deleted' ]] && ! grep -q exit-hold '${SIM}/policies'"
check "and the OCI Logging logs and their group" test "$(grep -c . "${SIM}/logs-deleted")" == 2 -a -f "${SIM}/log-group-deleted"

printf '\n%s\n' "$([[ "${failures}" -eq 0 ]] && echo 'all exit checks passed' || echo "${failures} exit check(s) failed")"
exit "$((failures > 0))"
