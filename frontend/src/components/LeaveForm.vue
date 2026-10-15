<script setup lang="ts">
/**
 * Leave application form with client-side checks that mirror the server's rules.
 *
 * Times are the hostel's wall-clock time (India), as the server reads them; "now" is the
 * hostel's clock, not the phone's, so a parent abroad is held to the same rule. Mess
 * reduction is offered by the server's count of full mess days missed (the noon rule), not by
 * calendar days, so the form never promises a reduction the server will not grant.
 */
import { CircleAlert, Globe } from "@lucide/vue";
import { computed, nextTick, reactive, ref } from "vue";
import type { LeaveInput } from "@/api/types";
import { t, tc } from "@/i18n";
import {
	deviceOffSiteTime,
	formatTime,
	fromDatetimeLocal,
	inclusiveDays,
	messDaysAway,
	normalizeMobile,
	parseDate,
	SITE_TIME_ZONE,
	siteNow,
	toDatetimeLocal,
} from "@/utils/format";
import FormField from "./FormField.vue";
import ToggleSwitch from "./ToggleSwitch.vue";

const props = withDefaults(
	defineProps<{
		busy?: boolean;
		serverError?: string;
		reductionMinDays?: number;
		/** Hour before which leaving (or at/after which returning) still counts that mess day. */
		cutoffHour?: number;
		timeZone?: string;
		/** The hostel's current time (tests pass a fixed clock). */
		now?: () => Date;
	}>(),
	{ reductionMinDays: 3, cutoffHour: 12, timeZone: SITE_TIME_ZONE, now: undefined },
);
const clock = (): Date => (props.now ? props.now() : siteNow(props.timeZone));
const emit = defineEmits<{ submit: [input: LeaveInput] }>();

/** Small allowance so a form opened a few minutes ago is not rejected for "past" times. */
const PAST_GRACE_MS = 10 * 60 * 1000;

const form = reactive({
	from_datetime: "",
	to_datetime: "",
	destination: "",
	reason: "",
	contact_during_leave: "",
	apply_mess_reduction: false,
});
const errors = reactive<Record<string, string>>({});
const root = ref<HTMLFormElement | null>(null);
const summary = ref("");

const minFrom = computed(() => toDatetimeLocal(clock()));
const days = computed(() => inclusiveDays(form.from_datetime, form.to_datetime));
const messDays = computed(() => messDaysAway(form.from_datetime, form.to_datetime, props.cutoffHour));
const longEnough = computed(() => messDays.value >= props.reductionMinDays);
const cutoffLabel = computed(() => formatTime(new Date(2000, 0, 1, props.cutoffHour, 0, 0)));
const reductionHint = computed(() => {
	const rule = { days: props.reductionMinDays, cutoff: cutoffLabel.value, count: messDays.value };
	if (!days.value) return t("leave.messReductionHint", rule);
	return longEnough.value ? tc("leave.messReductionCovers", messDays.value, rule) : tc("leave.messReductionTooShort", messDays.value, rule);
});
/** The phone's clock is not India time: say which clock the times are in. */
const showSiteTimeHint = computed(() => !props.now && deviceOffSiteTime(props.timeZone));

function validate(): boolean {
	for (const key of Object.keys(errors)) delete errors[key];
	const from = parseDate(form.from_datetime);
	const to = parseDate(form.to_datetime);

	if (!from) errors.from_datetime = t("validation.required", { field: t("leave.from") });
	else if (from.getTime() < clock().getTime() - PAST_GRACE_MS) errors.from_datetime = t("leave.leaveInPast");

	if (!to) errors.to_datetime = t("validation.required", { field: t("leave.to") });
	else if (from && to.getTime() <= from.getTime()) errors.to_datetime = t("leave.returnBeforeLeave");

	if (!form.destination.trim()) errors.destination = t("validation.required", { field: t("leave.destination") });
	if (!form.reason.trim()) errors.reason = t("validation.required", { field: t("leave.reason") });

	if (!form.contact_during_leave.trim()) {
		errors.contact_during_leave = t("validation.required", { field: t("leave.contact") });
	} else if (!normalizeMobile(form.contact_during_leave)) {
		errors.contact_during_leave = t("validation.phone");
	}
	return Object.keys(errors).length === 0;
}

async function submit() {
	if (!validate()) {
		summary.value = t("validation.summary", { count: Object.keys(errors).length });
		await nextTick();
		root.value?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
		return;
	}
	summary.value = "";
	emit("submit", {
		from_datetime: fromDatetimeLocal(form.from_datetime),
		to_datetime: fromDatetimeLocal(form.to_datetime),
		destination: form.destination.trim(),
		reason: form.reason.trim(),
		contact_during_leave: normalizeMobile(form.contact_during_leave),
		apply_mess_reduction: form.apply_mess_reduction && longEnough.value ? 1 : 0,
	});
}

defineExpose({ errors, form });
</script>

<template>
	<form ref="root" class="card" novalidate @submit.prevent="submit">
		<p v-if="summary" class="mb-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert" data-test="summary">
			<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ summary }}
		</p>

		<div class="grid gap-x-4 sm:grid-cols-2">
			<FormField v-slot="{ id, describedBy, invalid }" :label="t('leave.from')" :error="errors.from_datetime" required>
				<input
					:id="id"
					v-model="form.from_datetime"
					name="from_datetime"
					class="input"
					type="datetime-local"
					:min="minFrom"
					:aria-describedby="describedBy"
					:aria-invalid="invalid"
					required
				>
			</FormField>
			<FormField v-slot="{ id, describedBy, invalid }" :label="t('leave.to')" :error="errors.to_datetime" required>
				<input
					:id="id"
					v-model="form.to_datetime"
					name="to_datetime"
					class="input"
					type="datetime-local"
					:min="form.from_datetime || minFrom"
					:aria-describedby="describedBy"
					:aria-invalid="invalid"
					required
				>
			</FormField>
		</div>
		<p v-if="showSiteTimeHint" class="-mt-2 mb-3 flex items-start gap-1.5 text-sm text-muted" data-test="site-time-hint">
			<Globe :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />{{ t("leave.siteTimeHint") }}
		</p>
		<p v-if="days" class="-mt-2 mb-4 text-sm text-muted" aria-live="polite">{{ tc("common.days", days) }}</p>

		<FormField v-slot="{ id, describedBy, invalid }" :label="t('leave.destination')" :hint="t('leave.destinationHint')" :error="errors.destination" required>
			<input :id="id" v-model="form.destination" name="destination" class="input" type="text" maxlength="140" autocomplete="address-level2" :aria-describedby="describedBy" :aria-invalid="invalid" required>
		</FormField>

		<FormField v-slot="{ id, describedBy, invalid }" :label="t('leave.reason')" :error="errors.reason" required>
			<textarea :id="id" v-model="form.reason" name="reason" class="input" rows="3" maxlength="500" :aria-describedby="describedBy" :aria-invalid="invalid" required />
		</FormField>

		<FormField v-slot="{ id, describedBy, invalid }" :label="t('leave.contact')" :hint="t('leave.contactHint')" :error="errors.contact_during_leave" required>
			<input
				:id="id"
				v-model="form.contact_during_leave"
				name="contact_during_leave"
				class="input"
				type="tel"
				inputmode="numeric"
				autocomplete="tel-national"
				maxlength="14"
				:aria-describedby="describedBy"
				:aria-invalid="invalid"
				required
			>
		</FormField>

		<ToggleSwitch
			v-model="form.apply_mess_reduction"
			:label="t('leave.messReduction')"
			:hint="reductionHint"
			:disabled="Boolean(days) && !longEnough"
		/>

		<p v-if="serverError" class="mb-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
			<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ serverError }}
		</p>
		<button type="submit" class="btn-primary btn-block" :disabled="busy">
			{{ busy ? t("common.submitting") : t("leave.submit") }}
		</button>
	</form>
</template>
