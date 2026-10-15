<script setup lang="ts">
import { Camera, CircleAlert, FileText, LoaderCircle, ShieldAlert, X } from "@lucide/vue";
import { computed, nextTick, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { MAX_UPLOAD_BYTES } from "@/api/files";
import { raiseGrievance } from "@/api/portal";
import FormField from "@/components/FormField.vue";
import PageHeader from "@/components/PageHeader.vue";
import ToggleSwitch from "@/components/ToggleSwitch.vue";
import { GRIEVANCE_CATEGORIES, GRIEVANCE_PRIORITIES, MAX_GRIEVANCE_ATTACHMENTS, SENSITIVE_CATEGORY } from "@/constants";
import { useAttachments } from "@/composables/useAttachments";
import { useSubmit } from "@/composables/useSubmit";
import { t, tv } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";
import { formatBytes } from "@/utils/format";

const router = useRouter();
const { session } = useSession();
const { busy, error, run } = useSubmit();
const attachments = useAttachments(MAX_GRIEVANCE_ATTACHMENTS);

const form = reactive({ category: "", subject: "", description: "", priority: "Medium", is_confidential: false });
const errors = reactive<Record<string, string>>({});
const fileErrors = ref<string[]>([]);
const formRef = ref<HTMLFormElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

const sensitive = computed(() => form.category === SENSITIVE_CATEGORY);
const supportPhone = computed(() => session.settings.office_phone || session.settings.support_phone || "");

watch(sensitive, (value) => {
	if (value) {
		form.is_confidential = true;
		form.priority = "Urgent";
	}
});

function pickFiles(event: Event) {
	const input = event.target as HTMLInputElement;
	if (input.files?.length) fileErrors.value = attachments.add(input.files);
	input.value = "";
}

function validate(): boolean {
	for (const key of Object.keys(errors)) delete errors[key];
	if (!form.category) errors.category = t("validation.required", { field: t("grievances.category") });
	if (!form.subject.trim()) errors.subject = t("validation.required", { field: t("grievances.subject") });
	if (form.description.trim().length < 10) errors.description = t("grievances.describeMore");
	return Object.keys(errors).length === 0;
}

async function submit() {
	if (!validate()) {
		await nextTick();
		formRef.value?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
		return;
	}
	if (attachments.uploading.value) {
		error.value = t("grievances.waitForUploads");
		return;
	}
	const result = await run(() =>
		raiseGrievance(
			{
				category: form.category,
				subject: form.subject.trim(),
				description: form.description.trim(),
				priority: form.priority,
				is_confidential: form.is_confidential || sensitive.value ? 1 : 0,
				attachments: attachments.fileUrls.value,
			},
			studentArg(),
		),
	);
	if (result) {
		showToast(t("grievances.submitted", { name: result.name }));
		await router.push({ name: "grievance", params: { name: result.name } });
	}
}
</script>

<template>
	<div>
		<PageHeader :title="t('grievances.new')" :subtitle="t('grievances.newSubtitle')" :back="{ name: 'grievances' }" />

		<form ref="formRef" class="card" novalidate @submit.prevent="submit">
			<FormField v-slot="{ id, describedBy, invalid }" :label="t('grievances.category')" :error="errors.category" required>
				<select :id="id" v-model="form.category" class="input" :aria-describedby="describedBy" :aria-invalid="invalid" required>
					<option value="" disabled>{{ t("common.choose") }}</option>
					<option v-for="category in GRIEVANCE_CATEGORIES" :key="category" :value="category">{{ tv("grievanceCategories", category) }}</option>
				</select>
			</FormField>

			<div v-if="sensitive" class="mb-4 flex items-start gap-3 rounded-xl border border-danger bg-danger-soft p-3 text-sm text-ink" role="note">
				<ShieldAlert :size="20" class="mt-0.5 shrink-0 text-danger" aria-hidden="true" />
				<div>
					<p>{{ t("grievances.sensitiveNote") }}</p>
					<a v-if="supportPhone" :href="`tel:${supportPhone.replace(/\s/g, '')}`" class="mt-1 inline-flex min-h-11 items-center font-semibold">
						{{ t("grievances.callNow", { phone: supportPhone }) }}
					</a>
				</div>
			</div>

			<FormField v-slot="{ id, describedBy, invalid }" :label="t('grievances.subject')" :hint="t('grievances.subjectHint')" :error="errors.subject" required>
				<input :id="id" v-model="form.subject" class="input" type="text" maxlength="140" :aria-describedby="describedBy" :aria-invalid="invalid" required>
			</FormField>

			<FormField v-slot="{ id, describedBy, invalid }" :label="t('grievances.description')" :hint="t('grievances.descriptionHint')" :error="errors.description" required>
				<textarea :id="id" v-model="form.description" class="input" rows="5" maxlength="4000" :aria-describedby="describedBy" :aria-invalid="invalid" required />
			</FormField>

			<FormField v-slot="{ id, describedBy }" :label="t('grievances.priority')" required>
				<select :id="id" v-model="form.priority" class="input" :aria-describedby="describedBy" :disabled="sensitive">
					<option v-for="priority in GRIEVANCE_PRIORITIES" :key="priority" :value="priority">{{ tv("priorities", priority) }}</option>
				</select>
			</FormField>

			<ToggleSwitch
				v-model="form.is_confidential"
				:label="t('grievances.confidential')"
				:hint="t('grievances.confidentialHint')"
				:disabled="sensitive"
			/>

			<fieldset class="mb-5">
				<legend class="field-label">{{ t("grievances.attachments") }} <span class="font-normal text-muted">{{ t("common.optional") }}</span></legend>
				<p id="attach-hint" class="mb-2 text-sm text-muted">
					{{ t("grievances.attachmentHint", { count: MAX_GRIEVANCE_ATTACHMENTS, size: formatBytes(MAX_UPLOAD_BYTES) }) }}
				</p>
				<ul v-if="attachments.items.length" class="mb-3 grid grid-cols-3 gap-2 sm:grid-cols-5">
					<li v-for="item in attachments.items" :key="item.id" class="relative">
						<div class="flex aspect-square items-center justify-center overflow-hidden rounded-xl border border-line bg-surface-2" :class="item.status === 'failed' ? 'border-danger' : ''">
							<img v-if="item.preview" :src="item.preview" :alt="item.name" class="size-full object-cover">
							<FileText v-else :size="28" class="text-muted" aria-hidden="true" />
							<span v-if="item.status === 'uploading'" class="absolute inset-0 flex items-center justify-center rounded-xl bg-black/40 text-white">
								<LoaderCircle :size="24" class="motion-safe:animate-spin" aria-hidden="true" />
								<span class="sr-only">{{ t("grievances.uploading", { name: item.name }) }}</span>
							</span>
						</div>
						<button
							type="button"
							class="absolute -top-2 -right-2 inline-flex size-8 items-center justify-center rounded-full bg-ink text-bg shadow"
							:aria-label="t('grievances.removeFile', { name: item.name })"
							@click="attachments.remove(item.id)"
						>
							<X :size="16" aria-hidden="true" />
						</button>
						<p v-if="item.status === 'failed'" class="mt-1 text-xs font-medium text-danger" role="alert">{{ item.error }}</p>
					</li>
				</ul>
				<ul v-if="fileErrors.length" class="mb-2 space-y-1" role="alert">
					<li v-for="message in fileErrors" :key="message" class="field-error">{{ message }}</li>
				</ul>
				<input
					ref="fileInput"
					type="file"
					class="sr-only"
					accept="image/jpeg,image/png,image/webp,application/pdf"
					multiple
					aria-describedby="attach-hint"
					tabindex="-1"
					@change="pickFiles"
				>
				<button
					v-if="attachments.remaining.value > 0"
					type="button"
					class="btn-secondary"
					aria-describedby="attach-hint"
					@click="fileInput?.click()"
				>
					<Camera :size="18" aria-hidden="true" />{{ t("grievances.addFile") }}
				</button>
			</fieldset>

			<p v-if="error" class="mb-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
				<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ error }}
			</p>
			<button type="submit" class="btn-primary btn-block" :disabled="busy || attachments.uploading.value">
				{{ busy ? t("common.submitting") : attachments.uploading.value ? t("grievances.uploadingShort") : t("grievances.submit") }}
			</button>
		</form>
	</div>
</template>
