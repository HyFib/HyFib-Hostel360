#!/usr/bin/env bash
# HyFib Hostel360: destroy the OCI resources at the end of the contract (TR 22).
# Run by the tenancy administrator, from this directory, on a workstation with the OCI CLI
# (a profile with administrator rights in the tenancy), Terraform with this configuration's
# state, and jq. The order is set out in deploy/EXIT.md.
#
#   ./exit-purge.sh plan                       what would be deleted; nothing changes (default)
#   ./exit-purge.sh prepare-hold [--days N]    log hold bucket for the CERT-In logs, before
#                                              h360-decommission runs on the server
#   ./exit-purge.sh execute --certificate FILE [--server-lost REASON]
#                                              delete the backups and the infrastructure
#   ./exit-purge.sh release                    once the hold has expired: the hold bucket
#                                              and the OCI Logging logs
#
# Options: --profile NAME (OCI CLI profile; default $OCI_CLI_PROFILE or DEFAULT),
#          --records DIR (default ~/hostel360-exit): inventory, hold details and a log of
#          every action, kept as the evidence for the exit sign-off.
#
# prepare-hold makes <backup bucket>-exit-hold in the backup region (India): private,
# a retention rule of N days (default 181) so nothing in it can be changed or deleted before
# then, a lifecycle rule that deletes its objects a day later, and a policy that lets the
# server add objects to it, but not change or delete them.
#
# execute, after h360-decommission has finished on the server (its record must be in the
# hold bucket unless --server-lost explains why it cannot be):
#   1. removes the server's access to the hold bucket
#   2. deletes every object version and unfinished multipart upload in the backup bucket
#      (the restic repository); the server's own credentials could never delete versions
#   3. terraform destroy of everything else, with prevent_destroy lifted for this run only
#      (exit_override.tf, removed afterwards); the OCI Logging logs (CERT-In, 180 days) and
#      the Vault key are taken out of Terraform's state first and handled separately
#   4. deletes every block-volume and boot-volume backup of the server in both regions:
#      the scheduled ones, h360-snapshot's ad-hoc ones and cross-region copies (after the
#      destroy, so the backup policy cannot make a new one)
#   5. schedules the Vault key for deletion, then the vault: last, once nothing encrypted
#      with it is left
# Each of these asks for confirmation first: the backup bucket's name, ACGCET's
# acknowledgement reference from the deletion certificate, and DESTROY <name>; Terraform
# then shows its plan and asks once more.
#
# release refuses to run before the hold date. It empties and deletes the hold bucket and
# its policy, deletes the OCI Logging logs and their log group, and finishes the destroy if
# a network resource had to wait for the logs.
set -Eeuo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
cd "${here}"

die() {
	printf 'exit-purge: %s\n' "$*" >&2
	exit 1
}
say() {
	printf '%s\n' "$*"
}
indent() {
	printf '%s\n' "$1" | while IFS= read -r line; do printf '      %s\n' "${line}"; done
}

stage="${1:-plan}"
[[ $# -gt 0 ]] && shift
profile="${OCI_CLI_PROFILE:-DEFAULT}"
records="${HOME}/hostel360-exit"
hold_days=181
certificate=""
server_lost=""
while [[ $# -gt 0 ]]; do
	case "$1" in
		--profile)
			profile="${2:?--profile needs a name}"
			shift
			;;
		--records)
			records="${2:?--records needs a directory}"
			shift
			;;
		--days)
			hold_days="${2:?--days needs a number}"
			shift
			;;
		--certificate)
			certificate="${2:?--certificate needs the deletion certificate .json}"
			shift
			;;
		--server-lost)
			server_lost="${2:?--server-lost needs the reason}"
			shift
			;;
		-h | --help)
			sed -n '2,/^set -Eeuo/p' "$0" | sed '$d; s/^# \{0,1\}//'
			exit 0
			;;
		*) die "unknown option $1 (see $0 --help)" ;;
	esac
	shift
done
case "${stage}" in
	plan | prepare-hold | execute | release) ;;
	-h | --help)
		sed -n '2,/^set -Eeuo/p' "$0" | sed '$d; s/^# \{0,1\}//'
		exit 0
		;;
	*) die "unknown stage ${stage}: plan, prepare-hold, execute or release" ;;
esac
if [[ ! "${hold_days}" =~ ^[0-9]+$ ]] || ((hold_days < 181 || hold_days > 3650)); then
	die "--days must be at least 181 (CERT-In: 180 days)"
fi

for tool in oci terraform jq; do
	command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required"
done
mkdir -p "${records}"
chmod 0700 "${records}"
inventory="${records}/inventory.json"
hold_file="${records}/hold.json"
actions="${records}/actions.jsonl"

ocli() {
	oci --profile "${profile}" "$@"
}

operator=""
record() {
	# record <action> [detail]: one line per action, kept for the sign-off
	if [[ -z "${operator}" ]]; then
		operator="$(ocli iam user get --user-id "$(user_ocid)" --query 'data.name' --raw-output 2>/dev/null || true)"
		operator="${operator:-$(whoami)}"
	fi
	jq -cn --arg at "$(date '+%Y-%m-%dT%H:%M:%S%z')" --arg stage "${stage}" --arg action "$1" \
		--arg detail "${2:-}" --arg operator "${operator}" \
		'{at: $at, stage: $stage, action: $action, detail: $detail, operator: $operator}' >>"${actions}"
	say "  done: $1${2:+ ($2)}"
}

user_ocid() {
	# The operator as the OCI CLI knows them (config file profile)
	awk -v p="[${profile}]" '$0 == p { s = 1; next } /^\[/ { s = 0 } s && /^user *=/ { sub(/^user *= */, ""); print; exit }' \
		"${OCI_CLI_CONFIG_FILE:-${HOME}/.oci/config}" 2>/dev/null || true
}

confirm() {
	# confirm <prompt> <expected>: the exact answer or stop
	local answer
	printf '%s' "$1"
	IFS= read -r answer
	[[ "${answer}" == "$2" ]] || die "not confirmed; stopped before changing anything further"
}

inv() {
	jq -r --arg key "$1" '.[$key] // ""' "${inventory}"
}

# --------------------------------------------------------------------------- inventory

load_inventory() {
	local output
	if output="$(terraform output -json exit_inventory 2>/dev/null)" && [[ "$(jq -r 'type' <<<"${output}")" == object ]]; then
		jq . <<<"${output}" >"${inventory}.new"
		mv -f "${inventory}.new" "${inventory}"
		chmod 0600 "${inventory}"
	elif [[ ! -s "${inventory}" ]]; then
		die "terraform output exit_inventory is not available: run 'terraform init' here, then 'terraform apply -refresh-only' once (it only records outputs)"
	fi
	name="$(inv name)"
	compartment="$(inv compartment_id)"
	app_region="$(inv app_region)"
	backup_region="$(inv backup_region)"
	home_region="$(inv home_region)"
	namespace="$(inv namespace)"
	backup_bucket="$(inv backup_bucket)"
	data_volume="$(inv data_volume_id)"
	boot_volume="$(inv boot_volume_id)"
	dynamic_group="$(inv dynamic_group_name)"
	hold_bucket="${backup_bucket}-exit-hold"
	[[ -n "${compartment}" && -n "${backup_bucket}" && -n "${namespace}" ]] || die "the inventory in ${inventory} is incomplete"
}

bucket_exists() {
	ocli os bucket get --bucket-name "$1" --namespace "${namespace}" --region "${backup_region}" >/dev/null 2>&1
}

count_versions() {
	ocli os object list-object-versions --bucket-name "$1" --namespace "${namespace}" --region "${backup_region}" \
		--all --output json 2>/dev/null | jq '[.data[]?] | length' 2>/dev/null || echo 0
}

multipart_uploads() {
	ocli os multipart list --bucket-name "$1" --namespace "${namespace}" --region "${backup_region}" \
		--all --output json 2>/dev/null | jq -c '.data[]?' 2>/dev/null || true
}

volume_backups() {
	# volume_backups <region>: "<kind> <id> <name>" for every backup of the server's volumes
	local region="$1" selected
	selected="$(ocli bv backup list --compartment-id "${compartment}" --region "${region}" --all --output json 2>/dev/null |
		jq -r --arg data "${data_volume}" --arg name "${name}" '.data[]?
			| select(.["lifecycle-state"] != "TERMINATED" and .["lifecycle-state"] != "TERMINATING")
			| select(.["volume-id"] == $data or (.["freeform-tags"].application // "") == "hostel360"
				or ((.["display-name"] // "") | startswith("hostel360-")) or ((.["display-name"] // "") | startswith($name)))
			| "volume \(.id) \(.["display-name"])"' || true)"
	[[ -n "${selected}" ]] && printf '%s\n' "${selected}"
	selected="$(ocli bv boot-volume-backup list --compartment-id "${compartment}" --region "${region}" --all --output json 2>/dev/null |
		jq -r --arg boot "${boot_volume}" --arg name "${name}" '.data[]?
			| select(.["lifecycle-state"] != "TERMINATED" and .["lifecycle-state"] != "TERMINATING")
			| select(.["boot-volume-id"] == $boot or (.["freeform-tags"].application // "") == "hostel360"
				or ((.["display-name"] // "") | startswith($name)))
			| "boot \(.id) \(.["display-name"])"' || true)"
	[[ -n "${selected}" ]] && printf '%s\n' "${selected}"
	return 0
}

retention_rules() {
	# ids of the hold bucket's retention rules (the CLI wraps the list in data.items)
	ocli os retention-rule list --bucket-name "${hold_bucket}" --namespace "${namespace}" \
		--region "${backup_region}" --all --output json 2>/dev/null |
		jq -r '.data | (if type == "object" then (.items // []) else (. // []) end) | .[] | .id' 2>/dev/null || true
}

other_backups() {
	# other_backups <region>: backups in the compartment that volume_backups does not select
	local region="$1" chosen
	chosen="$(volume_backups "${region}" | awk '{ print $2 }')"
	{
		ocli bv backup list --compartment-id "${compartment}" --region "${region}" --all --output json 2>/dev/null |
			jq -r '.data[]? | select(.["lifecycle-state"] != "TERMINATED") | "\(.id) \(.["display-name"])"' || true
		ocli bv boot-volume-backup list --compartment-id "${compartment}" --region "${region}" --all --output json 2>/dev/null |
			jq -r '.data[]? | select(.["lifecycle-state"] != "TERMINATED") | "\(.id) \(.["display-name"])"' || true
	} | while IFS=' ' read -r id display; do
		[[ -n "${id}" ]] || continue
		grep -qxF "${id}" <<<"${chosen}" || printf '%s %s\n' "${id}" "${display}"
	done
}

state_resources() {
	terraform state list 2>/dev/null | grep -v '^data\.' || true
}

show_plan() {
	local versions uploads backups region
	say ""
	say "Hostel360 exit: ${name} in compartment ${compartment}"
	say "  regions: application ${app_region}, backups ${backup_region}, identity ${home_region}"
	if bucket_exists "${backup_bucket}"; then
		versions="$(count_versions "${backup_bucket}")"
		uploads="$(multipart_uploads "${backup_bucket}" | grep -c . || true)"
		say "  backup bucket ${backup_bucket}: ${versions} object versions, ${uploads} unfinished uploads"
	else
		say "  backup bucket ${backup_bucket}: gone"
	fi
	if bucket_exists "${hold_bucket}"; then
		say "  log hold bucket ${hold_bucket}: $(count_versions "${hold_bucket}") objects$([[ -s "${hold_file}" ]] && printf ', held until %s' "$(jq -r .until "${hold_file}")")"
	else
		say "  log hold bucket ${hold_bucket}: not made yet (prepare-hold)"
	fi
	for region in "${app_region}" "${backup_region}"; do
		backups="$(volume_backups "${region}")"
		say "  volume and boot-volume backups in ${region}: $(grep -c . <<<"${backups}" || true)"
		[[ -n "${backups}" ]] && indent "${backups}"
		backups="$(other_backups "${region}")"
		if [[ -n "${backups}" ]]; then
			say "  other backups in the compartment in ${region}, NOT deleted (delete by hand if they are Hostel360's):"
			indent "${backups}"
		fi
	done
	say "  Terraform resources: $(state_resources | grep -c . || true)"
	say "  OCI Logging: log group $(inv log_group_id) with $(jq '.log_ids | length' "${inventory}") logs (kept for the hold)"
	if [[ -n "$(inv key_id)" ]]; then
		say "  Vault key $(inv key_id), vault $(inv vault_id): scheduled for deletion last"
	fi
	say ""
}

# --------------------------------------------------------------------------- terraform

override="${here}/exit_override.tf"
remove_override() {
	rm -f -- "${override}"
}

write_override() {
	# Lifts prevent_destroy for this run only; the resource files themselves keep it.
	cat >"${override}" <<'EOF'
# Written by exit-purge.sh for the exit only and removed when it finishes.
resource "oci_objectstorage_bucket" "backups" {
  lifecycle {
    prevent_destroy = false
  }
}

resource "oci_core_volume" "data" {
  lifecycle {
    prevent_destroy = false
  }
}

resource "oci_core_public_ip" "lb" {
  lifecycle {
    prevent_destroy = false
  }
}

resource "oci_kms_key" "volumes" {
  lifecycle {
    prevent_destroy = false
  }
}
EOF
	trap remove_override EXIT
}

destroy_infrastructure() {
	local address left
	write_override
	terraform init -input=false >/dev/null
	# Kept out of the destroy: the CERT-In logs (released later) and the Vault key and vault
	# (scheduled for deletion only once nothing encrypted with them is left).
	while IFS= read -r address; do
		case "${address}" in
			oci_logging_log_group.* | oci_logging_log.* | oci_kms_vault.* | oci_kms_key.*)
				terraform state rm "${address}" >/dev/null
				record "removed from Terraform state" "${address}"
				;;
		esac
	done < <(state_resources)
	say "Terraform shows what it will destroy and asks for 'yes':"
	if ! terraform destroy -input=true; then
		say "terraform destroy did not finish."
	fi
	left="$(state_resources)"
	if [[ -n "${left}" ]]; then
		say "Still in Terraform's state:"
		indent "${left}"
		# Only network resources may wait (a subnet still referred to by its flow log).
		if grep -vqE '^oci_core_(vcn|subnet|security_list|network_security_group|network_security_group_security_rule|route_table|internet_gateway|nat_gateway|service_gateway)\.' <<<"${left}"; then
			die "resources other than network ones are left; fix the error above and run execute again"
		fi
		record "terraform destroy partial" "network resources wait for the log release: $(tr '\n' ' ' <<<"${left}")"
	else
		record "terraform destroy" "no resources left in the state"
	fi
}

# --------------------------------------------------------------------------- stages

load_inventory

case "${stage}" in
	plan)
		show_plan
		say "Nothing was changed. Stages: prepare-hold, execute, release (deploy/EXIT.md)."
		;;

	prepare-hold)
		show_plan
		until_date="$(jq -rn --argjson days "${hold_days}" 'now + $days * 86400 | strftime("%Y-%m-%d")')"
		[[ -t 0 ]] || die "prepare-hold needs an interactive terminal"
		confirm "Create ${hold_bucket} in ${backup_region}, locked until ${until_date}? Type the backup bucket name (${backup_bucket}): " "${backup_bucket}"
		if ! bucket_exists "${hold_bucket}"; then
			ocli os bucket create --name "${hold_bucket}" --compartment-id "${compartment}" \
				--namespace "${namespace}" --region "${backup_region}" --public-access-type NoPublicAccess \
				--storage-tier Standard --versioning Disabled \
				--freeform-tags '{"application": "hostel360", "purpose": "cert-in-log-hold"}' >/dev/null
			record "hold bucket created" "${hold_bucket} (${backup_region})"
		fi
		rules="$(retention_rules | grep -c . || true)"
		if [[ "${rules}" == 0 ]]; then
			ocli os retention-rule create --bucket-name "${hold_bucket}" --namespace "${namespace}" \
				--region "${backup_region}" --display-name cert-in-log-hold \
				--time-amount "${hold_days}" --time-unit DAYS >/dev/null
			record "retention rule" "${hold_days} days from each object's upload"
		fi
		policy_statements() {
			jq -cn --arg region "${backup_region}" --arg cp "compartment id ${compartment}" \
				--arg bucket "${hold_bucket}" --arg dg "${dynamic_group}" --arg kind "$1" '
				if $kind == "lifecycle" then
					["Allow service objectstorage-\($region) to manage object-family in \($cp) where target.bucket.name = '"'"'\($bucket)'"'"'"]
				else
					["Allow dynamic-group \($dg) to read buckets in \($cp) where target.bucket.name = '"'"'\($bucket)'"'"'",
					 "Allow dynamic-group \($dg) to manage objects in \($cp) where all {target.bucket.name = '"'"'\($bucket)'"'"', any {request.permission = '"'"'OBJECT_CREATE'"'"', request.permission = '"'"'OBJECT_INSPECT'"'"', request.permission = '"'"'OBJECT_READ'"'"'}}"]
				end'
		}
		for kind in lifecycle server; do
			policy="${name}-exit-hold-${kind}"
			existing="$(ocli iam policy list --compartment-id "${compartment}" --region "${home_region}" --all \
				--output json 2>/dev/null | jq -r --arg name "${policy}" '.data[]? | select(.name == $name) | .id' | head -n 1)"
			if [[ -z "${existing}" ]]; then
				ocli iam policy create --compartment-id "${compartment}" --region "${home_region}" --name "${policy}" \
					--description "HyFib Hostel360 exit: CERT-In log hold (${kind})" \
					--statements "$(policy_statements "${kind}")" >/dev/null
				record "policy created" "${policy}"
			fi
		done
		# The lifecycle rule needs the service policy; IAM takes a little while to apply it.
		lifecycle="$(jq -cn --argjson days "$((hold_days + 1))" \
			'[{name: "delete-after-hold", action: "DELETE", timeAmount: $days, timeUnit: "DAYS", isEnabled: true, target: "objects"},
			  {name: "abort-unfinished-uploads", action: "ABORT", timeAmount: 7, timeUnit: "DAYS", isEnabled: true, target: "multipart-uploads"}]')"
		for attempt in 1 2 3 4 5 6; do
			if ocli os object-lifecycle-policy put --bucket-name "${hold_bucket}" --namespace "${namespace}" \
				--region "${backup_region}" --items "${lifecycle}" --force >/dev/null 2>&1; then
				record "lifecycle rule" "objects deleted $((hold_days + 1)) days after upload"
				break
			fi
			((attempt < 6)) || die "the lifecycle rule could not be set; run prepare-hold again in a few minutes"
			sleep 20
		done
		jq -n --arg bucket "${hold_bucket}" --arg region "${backup_region}" --arg namespace "${namespace}" \
			--argjson days "${hold_days}" --arg until "${until_date}" --arg prepared "$(date '+%Y-%m-%dT%H:%M:%S%z')" \
			'{bucket: $bucket, region: $region, namespace: $namespace, days: $days, until: $until, prepared: $prepared}' \
			>"${hold_file}"
		say "Log hold ready: ${hold_bucket}. Now run h360-decommission on the server (deploy/EXIT.md, step 4)."
		;;

	execute)
		[[ -n "${certificate}" && -f "${certificate}" ]] || die "--certificate <deletion certificate .json> is required"
		jq -e '.certificate.certificate_id and .signature.value' "${certificate}" >/dev/null 2>&1 ||
			die "${certificate} is not a Hostel360 deletion certificate"
		certificate_id="$(jq -r '.certificate.certificate_id' "${certificate}")"
		reference="$(jq -r '.certificate.acknowledgement_reference // ""' "${certificate}")"
		[[ -s "${hold_file}" ]] || [[ -n "${server_lost}" ]] ||
			die "no log hold was prepared (prepare-hold); the CERT-In logs must be kept before anything is destroyed"
		[[ -t 0 ]] || die "execute needs an interactive terminal"

		if [[ -z "${server_lost}" ]]; then
			latest="$(ocli os object list --bucket-name "${hold_bucket}" --namespace "${namespace}" --region "${backup_region}" \
				--all --output json 2>/dev/null | jq -r '[.data[]? | select(.name | endswith("/decommission-record.json")) | .name] | sort | last // ""')"
			[[ -n "${latest}" ]] || die "no decommission record in ${hold_bucket}: run h360-decommission on the server first (or --server-lost REASON)"
			decommission="$(ocli os object get --bucket-name "${hold_bucket}" --namespace "${namespace}" \
				--region "${backup_region}" --name "${latest}" --file - 2>/dev/null)"
			[[ "$(jq -r .status <<<"${decommission}")" == completed && "$(jq -r .certificate_id <<<"${decommission}")" == "${certificate_id}" ]] ||
				die "${latest} is not a completed decommission for certificate ${certificate_id}"
			say "Server decommissioned: ${latest} ($(jq -r .finished_at <<<"${decommission}" 2>/dev/null || true))"
		else
			say "Server decommission record not required: ${server_lost}"
		fi

		show_plan
		say "Deletion certificate ${certificate_id}, acknowledgement ${reference:-none}"
		say "This deletes every backup and the whole installation. It cannot be undone."
		confirm "Type the backup bucket name (${backup_bucket}): " "${backup_bucket}"
		if [[ -n "${reference}" ]]; then
			confirm "Type ACGCET's acknowledgement reference from the certificate: " "${reference}"
		fi
		confirm "Type DESTROY ${name} to go ahead: " "DESTROY ${name}"
		record "confirmed" "certificate ${certificate_id}${server_lost:+; server lost: ${server_lost}}"
		if [[ ! -s "${hold_file}" ]]; then
			# No log hold bucket (server lost): the OCI Logging logs are still kept 181 days.
			jq -n --argjson days "${hold_days}" --arg prepared "$(date '+%Y-%m-%dT%H:%M:%S%z')" \
				'{bucket: "", days: $days, until: (now + $days * 86400 | strftime("%Y-%m-%d")), prepared: $prepared}' >"${hold_file}"
		fi

		# 1. the server no longer needs to reach the hold bucket
		policy="$(ocli iam policy list --compartment-id "${compartment}" --region "${home_region}" --all \
			--output json 2>/dev/null | jq -r --arg name "${name}-exit-hold-server" '.data[]? | select(.name == $name) | .id' | head -n 1)"
		if [[ -n "${policy}" ]]; then
			ocli iam policy delete --policy-id "${policy}" --region "${home_region}" --force >/dev/null
			record "policy deleted" "${name}-exit-hold-server"
		fi

		# 2. the backup bucket: every version and unfinished upload
		if bucket_exists "${backup_bucket}"; then
			ocli os object bulk-delete-versions --bucket-name "${backup_bucket}" --namespace "${namespace}" \
				--region "${backup_region}" --force >/dev/null
			while IFS= read -r upload; do
				[[ -n "${upload}" ]] || continue
				ocli os multipart abort --bucket-name "${backup_bucket}" --namespace "${namespace}" \
					--region "${backup_region}" --object-name "$(jq -r .object <<<"${upload}")" \
					--upload-id "$(jq -r '.["upload-id"]' <<<"${upload}")" --force >/dev/null
			done < <(multipart_uploads "${backup_bucket}")
			left="$(count_versions "${backup_bucket}")"
			[[ "${left}" == 0 && -z "$(multipart_uploads "${backup_bucket}")" ]] ||
				die "the backup bucket still holds ${left} object versions or unfinished uploads; run execute again"
			record "backup bucket emptied" "${backup_bucket}: all object versions and unfinished uploads deleted"
		fi

		# 3. the infrastructure
		destroy_infrastructure

		# 4. volume and boot-volume backups in both regions
		for region in "${app_region}" "${backup_region}"; do
			while IFS=' ' read -r kind id display; do
				[[ -n "${id:-}" ]] || continue
				if [[ "${kind}" == boot ]]; then
					ocli bv boot-volume-backup delete --boot-volume-backup-id "${id}" --region "${region}" --force >/dev/null
				else
					ocli bv backup delete --volume-backup-id "${id}" --region "${region}" --force >/dev/null
				fi
				record "$([[ "${kind}" == boot ]] && echo boot || echo block) volume backup deleted" "${display} ${id} (${region})"
			done < <(volume_backups "${region}")
		done
		for attempt in $(seq 1 60); do
			left="$(volume_backups "${app_region}"; volume_backups "${backup_region}")"
			[[ -z "${left}" ]] && break
			((attempt < 60)) || die "volume backups are still being deleted: ${left}"
			sleep 30
		done
		record "volume backups" "none left in ${app_region} or ${backup_region}"

		# 5. the Vault key, then the vault
		if [[ -n "$(inv key_id)" ]]; then
			ocli kms management key schedule-deletion --key-id "$(inv key_id)" \
				--endpoint "$(inv key_management_endpoint)" --region "${app_region}" >/dev/null
			record "Vault key scheduled for deletion" "$(inv key_id)"
			ocli kms management vault schedule-deletion --vault-id "$(inv vault_id)" --region "${app_region}" >/dev/null
			record "vault scheduled for deletion" "$(inv vault_id)"
		fi

		say ""
		say "Done. Still to do by hand (deploy/EXIT.md, step 6): the Uptime Kuma monitors and relay,"
		say "the portal's DNS record (ACGCET), the Terraform state bucket's old versions, and the"
		say "release of the log hold on or after $(jq -r '.until // "the hold date"' "${hold_file}" 2>/dev/null || echo 'the hold date')."
		say "Actions: ${actions}"
		;;

	release)
		[[ -s "${hold_file}" ]] || die "no hold recorded in ${hold_file}"
		until_date="$(jq -r .until "${hold_file}")"
		today="$(jq -rn 'now | strftime("%Y-%m-%d")')"
		[[ ! "${today}" < "${until_date}" ]] || die "the log hold runs until ${until_date}; it may not be released before then"
		[[ -t 0 ]] || die "release needs an interactive terminal"
		show_plan
		confirm "Delete the log hold bucket ${hold_bucket} and the OCI Logging logs? Type RELEASE ${name}: " "RELEASE ${name}"
		if bucket_exists "${hold_bucket}"; then
			ocli os object bulk-delete --bucket-name "${hold_bucket}" --namespace "${namespace}" \
				--region "${backup_region}" --force >/dev/null
			while IFS= read -r rule; do
				[[ -n "${rule}" ]] || continue
				ocli os retention-rule delete --bucket-name "${hold_bucket}" --namespace "${namespace}" \
					--region "${backup_region}" --retention-rule-id "${rule}" --force >/dev/null
			done < <(retention_rules)
			ocli os object-lifecycle-policy delete --bucket-name "${hold_bucket}" --namespace "${namespace}" \
				--region "${backup_region}" --force >/dev/null 2>&1 || true
			ocli os bucket delete --bucket-name "${hold_bucket}" --namespace "${namespace}" \
				--region "${backup_region}" --force >/dev/null
			record "hold bucket deleted" "${hold_bucket}"
		fi
		for kind in lifecycle server; do
			policy="$(ocli iam policy list --compartment-id "${compartment}" --region "${home_region}" --all \
				--output json 2>/dev/null | jq -r --arg name "${name}-exit-hold-${kind}" '.data[]? | select(.name == $name) | .id' | head -n 1)"
			if [[ -n "${policy}" ]]; then
				ocli iam policy delete --policy-id "${policy}" --region "${home_region}" --force >/dev/null
				record "policy deleted" "${name}-exit-hold-${kind}"
			fi
		done
		log_group="$(inv log_group_id)"
		while IFS= read -r log; do
			[[ -n "${log}" ]] || continue
			ocli logging log delete --log-group-id "${log_group}" --log-id "${log}" --region "${app_region}" \
				--force >/dev/null 2>&1 && record "log deleted" "${log}"
		done < <(jq -r '.log_ids[]?' "${inventory}")
		if [[ -n "${log_group}" ]]; then
			ocli logging log-group delete --log-group-id "${log_group}" --region "${app_region}" --force >/dev/null 2>&1 &&
				record "log group deleted" "${log_group}"
		fi
		if [[ -n "$(state_resources)" ]]; then
			destroy_infrastructure
		fi
		say "Released. Actions: ${actions}"
		;;
esac
