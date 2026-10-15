<script setup lang="ts">
import { Award, CircleAlert, Download } from "@lucide/vue";
import { computed, reactive, ref } from "vue";
import { downloadBonafide, listCertificates, requestBonafide } from "@/api/portal";
import BaseDialog from "@/components/BaseDialog.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import FormField from "@/components/FormField.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import StatusChip from "@/components/StatusChip.vue";
import { BONAFIDE_PURPOSES } from "@/constants";
import { useDownload } from "@/composables/useDownload";
import { useResource } from "@/composables/useResource";
import { useSubmit } from "@/composables/useSubmit";
import { t, tv } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";
import { formatDate } from "@/utils/format";

const { session } = useSession();
const certificates = useResource(() => listCertificates(studentArg()), { watch: [() => session.activeStudent] });
const list = computed(() => certificates.data.value ?? []);
const { busyKey, download } = useDownload();
const { busy, error, run } = useSubmit();

const open = ref(false);
const form = reactive({ purpose: "", details: "" });
const errors = reactive<Record<string, string>>({});
const hasPending = computed(() => list.value.some((item) => item.status === "Pending"));

function startRequest() {
	form.purpose = "";
	form.details = "";
	error.value = "";
	for (const key of Object.keys(errors)) delete errors[key];
	open.value = true;
}

async function submit() {
	for (const key of Object.keys(errors)) delete errors[key];
	if (!form.purpose) errors.purpose = t("validation.required", { field: t("certificates.purpose") });
	if (form.purpose === "Other" && !form.details.trim()) errors.details = t("certificates.detailsRequired");
	if (Object.keys(errors).length) return;
	const result = await run(() => requestBonafide(form.purpose, form.details.trim(), studentArg()));
	if (result) {
		open.value = false;
		showToast(t("certificates.submitted"));
		await certificates.reload();
	}
}
</script>

<template>
	<div>
		<PageHeader :title="t('certificates.title')" :subtitle="t('certificates.subtitle')">
			<template #actions>
				<button type="button" class="btn-primary min-h-10 px-3 text-sm" @click="startRequest">{{ t("certificates.request") }}</button>
			</template>
		</PageHeader>

		<p v-if="hasPending" class="card mb-4 bg-info-soft text-sm text-info" role="status">{{ t("certificates.pendingNote") }}</p>

		<SkeletonList v-if="certificates.loading.value && !certificates.data.value" />
		<ErrorState v-else-if="certificates.error.value" :error="certificates.error.value" @retry="certificates.reload" />
		<EmptyState v-else-if="!list.length" :icon="Award" :title="t('certificates.empty')" :body="t('certificates.emptyHint')">
			<button type="button" class="btn-primary" @click="startRequest">{{ t("certificates.request") }}</button>
		</EmptyState>

		<ul v-else class="space-y-3">
			<li v-for="item in list" :key="item.name" class="card">
				<div class="flex flex-wrap items-start justify-between gap-2">
					<div class="min-w-0">
						<p class="font-semibold text-ink">{{ t("certificates.bonafide") }} · {{ tv("purposes", item.purpose) }}</p>
						<p v-if="item.purpose_details" class="text-sm text-muted">{{ item.purpose_details }}</p>
					</div>
					<StatusChip :status="item.is_expired ? 'Expired' : item.status" />
				</div>
				<dl class="mt-2 space-y-0.5 text-sm text-muted">
					<div v-if="item.certificate_number">{{ t("certificates.number", { number: item.certificate_number }) }}</div>
					<div v-if="item.issue_date">{{ t("certificates.issued", { date: formatDate(item.issue_date) }) }}</div>
					<div v-if="item.valid_till">{{ t("certificates.validTill", { date: formatDate(item.valid_till) }) }}</div>
					<div v-if="!item.issue_date && item.creation">{{ t("certificates.requestedOn", { date: formatDate(item.creation) }) }}</div>
				</dl>
				<p v-if="item.rejection_reason" class="mt-2 rounded-xl bg-danger-soft p-3 text-sm text-danger">
					<span class="font-semibold">{{ t("certificates.rejectionReason") }}:</span> {{ item.rejection_reason }}
				</p>
				<button
					v-if="item.can_download"
					type="button"
					class="btn-secondary mt-3"
					:disabled="busyKey === item.name"
					@click="download(item.name, () => downloadBonafide(item.name))"
				>
					<Download :size="18" aria-hidden="true" />
					{{ busyKey === item.name ? t("common.downloading") : t("certificates.download") }}
				</button>
			</li>
		</ul>

		<BaseDialog :open="open" :title="t('certificates.request')" @close="open = false">
			<form novalidate @submit.prevent="submit">
				<FormField v-slot="{ id, describedBy, invalid }" :label="t('certificates.purpose')" :error="errors.purpose" required>
					<select :id="id" v-model="form.purpose" class="input" data-autofocus :aria-describedby="describedBy" :aria-invalid="invalid">
						<option value="" disabled>{{ t("common.choose") }}</option>
						<option v-for="purpose in BONAFIDE_PURPOSES" :key="purpose" :value="purpose">{{ tv("purposes", purpose) }}</option>
					</select>
				</FormField>
				<FormField
					v-slot="{ id, describedBy, invalid }"
					:label="t('certificates.details')"
					:hint="t('certificates.detailsHint')"
					:error="errors.details"
					:required="form.purpose === 'Other'"
				>
					<textarea :id="id" v-model="form.details" class="input min-h-20" rows="3" maxlength="300" :aria-describedby="describedBy" :aria-invalid="invalid" />
				</FormField>
				<p v-if="error" class="mb-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
					<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ error }}
				</p>
				<div class="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
					<button type="button" class="btn-secondary" @click="open = false">{{ t("common.cancel") }}</button>
					<button type="submit" class="btn-primary" :disabled="busy">{{ busy ? t("common.submitting") : t("certificates.submit") }}</button>
				</div>
			</form>
		</BaseDialog>
	</div>
</template>
