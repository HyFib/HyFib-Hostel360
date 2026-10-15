<script setup lang="ts">
import { CircleAlert, Info } from "@lucide/vue";
import { computed, nextTick, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { applyMessReduction } from "@/api/portal";
import { REDUCTION_REASONS } from "@/constants";
import FormField from "@/components/FormField.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useSubmit } from "@/composables/useSubmit";
import { t, tc, tv } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";
import { inclusiveDays, toISODate } from "@/utils/format";

const router = useRouter();
const { reductionMinDays } = useSession();
const { busy, error, run } = useSubmit();

const tomorrow = new Date();
tomorrow.setDate(tomorrow.getDate() + 1);
const minDate = toISODate(tomorrow);

const form = reactive({ from_date: "", to_date: "", reason: "", description: "" });
const errors = reactive<Record<string, string>>({});
const formRef = ref<HTMLFormElement | null>(null);

const days = computed(() => inclusiveDays(form.from_date, form.to_date));

function validate(): boolean {
	for (const key of Object.keys(errors)) delete errors[key];
	if (!form.from_date) errors.from_date = t("validation.required", { field: t("reduction.from") });
	if (!form.to_date) errors.to_date = t("validation.required", { field: t("reduction.to") });
	if (form.from_date && form.to_date && form.to_date < form.from_date) errors.to_date = t("validation.endBeforeStart");
	else if (form.from_date && form.to_date && days.value < reductionMinDays.value) {
		errors.to_date = t("reduction.tooShort", { days: reductionMinDays.value, count: days.value });
	}
	if (!form.reason) errors.reason = t("validation.required", { field: t("reduction.reason") });
	if (form.reason === "Other" && !form.description.trim()) {
		errors.description = t("reduction.describeOther");
	}
	return Object.keys(errors).length === 0;
}

async function submit() {
	if (!validate()) {
		await nextTick();
		formRef.value?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
		return;
	}
	const result = await run(() =>
		applyMessReduction(
			{ from_date: form.from_date, to_date: form.to_date, reason: form.reason, description: form.description.trim() || undefined },
			studentArg(),
		),
	);
	if (result) {
		showToast(t("reduction.submitted"));
		await router.push({ name: "mess-reduction" });
	}
}
</script>

<template>
	<div>
		<PageHeader :title="t('reduction.new')" :back="{ name: 'mess-reduction' }" />

		<p class="card mb-5 flex items-start gap-2 bg-info-soft text-sm text-info">
			<Info :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />
			{{ t("reduction.minDaysHint", { days: reductionMinDays }) }}
		</p>

		<form ref="formRef" class="card" novalidate @submit.prevent="submit">
			<div class="grid gap-x-4 sm:grid-cols-2">
				<FormField v-slot="{ id, describedBy, invalid }" :label="t('reduction.from')" :error="errors.from_date" required>
					<input :id="id" v-model="form.from_date" class="input" type="date" :min="minDate" :aria-describedby="describedBy" :aria-invalid="invalid" required>
				</FormField>
				<FormField v-slot="{ id, describedBy, invalid }" :label="t('reduction.to')" :error="errors.to_date" required>
					<input :id="id" v-model="form.to_date" class="input" type="date" :min="form.from_date || minDate" :aria-describedby="describedBy" :aria-invalid="invalid" required>
				</FormField>
			</div>
			<p v-if="days" class="-mt-2 mb-4 text-sm font-medium" :class="days < reductionMinDays ? 'text-warning' : 'text-success'" aria-live="polite">
				{{ tc("common.days", days) }}
			</p>

			<FormField v-slot="{ id, describedBy, invalid }" :label="t('reduction.reason')" :error="errors.reason" required>
				<select :id="id" v-model="form.reason" class="input" :aria-describedby="describedBy" :aria-invalid="invalid" required>
					<option value="" disabled>{{ t("common.choose") }}</option>
					<option v-for="reason in REDUCTION_REASONS" :key="reason" :value="reason">{{ tv("reductionReasons", reason) }}</option>
				</select>
			</FormField>

			<FormField
				v-slot="{ id, describedBy, invalid }"
				:label="t('reduction.description')"
				:hint="t('reduction.descriptionHint')"
				:error="errors.description"
				:required="form.reason === 'Other'"
			>
				<textarea :id="id" v-model="form.description" class="input" rows="3" maxlength="500" :aria-describedby="describedBy" :aria-invalid="invalid" />
			</FormField>

			<p v-if="error" class="mb-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
				<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ error }}
			</p>
			<button type="submit" class="btn-primary btn-block" :disabled="busy">
				{{ busy ? t("common.submitting") : t("reduction.submit") }}
			</button>
		</form>
	</div>
</template>
