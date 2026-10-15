<script setup lang="ts">
import { CircleAlert, CircleCheck } from "@lucide/vue";
import { computed, reactive, ref, watch } from "vue";
import { submitFeedback } from "@/api/portal";
import FormField from "@/components/FormField.vue";
import PageHeader from "@/components/PageHeader.vue";
import StarRating from "@/components/StarRating.vue";
import ToggleSwitch from "@/components/ToggleSwitch.vue";
import { FEEDBACK_QUESTIONS, FEEDBACK_TYPES } from "@/constants";
import { useSubmit } from "@/composables/useSubmit";
import { t, tv } from "@/i18n";
import { showToast } from "@/stores/toast";
import { currentPeriod } from "@/utils/format";

type FeedbackType = (typeof FEEDBACK_TYPES)[number];
type Question = "food_quality" | "cleanliness" | "staff_behaviour" | "facilities";

const { busy, error, run } = useSubmit();
const feedbackType = ref<FeedbackType>("Mess");
const ratings = reactive<Record<Question | "overall", number>>({
	food_quality: 0,
	cleanliness: 0,
	staff_behaviour: 0,
	facilities: 0,
	overall: 0,
});
const comments = ref("");
const anonymous = ref(false);
const overallError = ref("");
const done = ref(false);

const questions = computed(() => FEEDBACK_QUESTIONS[feedbackType.value] as Question[]);

watch(feedbackType, () => {
	for (const key of Object.keys(ratings) as (keyof typeof ratings)[]) ratings[key] = 0;
});

async function submit() {
	overallError.value = "";
	if (!ratings.overall) {
		overallError.value = t("feedback.overallRequired");
		document.querySelector<HTMLInputElement>('fieldset[data-overall] input')?.focus();
		return;
	}
	const answers: Partial<Record<Question, number>> = {};
	for (const question of questions.value) if (ratings[question]) answers[question] = ratings[question];
	const result = await run(() =>
		submitFeedback({
			feedback_type: feedbackType.value,
			period: currentPeriod(),
			...answers,
			overall: ratings.overall,
			comments: comments.value.trim() || undefined,
			is_anonymous: anonymous.value ? 1 : 0,
		}),
	);
	if (result) {
		done.value = true;
		showToast(t("feedback.submitted"));
	}
}

function again() {
	done.value = false;
	comments.value = "";
	anonymous.value = false;
	for (const key of Object.keys(ratings) as (keyof typeof ratings)[]) ratings[key] = 0;
}
</script>

<template>
	<div>
		<PageHeader :title="t('feedback.title')" :subtitle="t('feedback.subtitle')" />

		<section v-if="done" class="card p-6 text-center" role="status">
			<CircleCheck :size="44" class="mx-auto text-success" aria-hidden="true" />
			<p class="mt-3 text-lg font-semibold text-ink">{{ t("feedback.thanksTitle") }}</p>
			<p class="mt-1 text-muted">{{ t("feedback.thanksBody") }}</p>
			<button type="button" class="btn-secondary mt-5" @click="again">{{ t("feedback.another") }}</button>
		</section>

		<form v-else class="card" novalidate @submit.prevent="submit">
			<FormField v-slot="{ id, describedBy }" :label="t('feedback.type')" required>
				<select :id="id" v-model="feedbackType" class="input" :aria-describedby="describedBy">
					<option v-for="type in FEEDBACK_TYPES" :key="type" :value="type">{{ tv("feedbackTypes", type) }}</option>
				</select>
			</FormField>

			<StarRating v-for="question in questions" :key="question" v-model="ratings[question]" :label="t(`feedback.questions.${question}`)" />
			<StarRating v-model="ratings.overall" :label="t('feedback.questions.overall')" :error="overallError" data-overall />

			<FormField v-slot="{ id, describedBy }" :label="t('feedback.comments')" :hint="t('feedback.commentsHint')">
				<textarea :id="id" v-model="comments" class="input" rows="4" maxlength="2000" :aria-describedby="describedBy" />
			</FormField>

			<ToggleSwitch v-model="anonymous" :label="t('feedback.anonymous')" :hint="t('feedback.anonymousHint')" />

			<p v-if="error" class="mb-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
				<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ error }}
			</p>
			<button type="submit" class="btn-primary btn-block" :disabled="busy">{{ busy ? t("common.submitting") : t("feedback.submit") }}</button>
		</form>
	</div>
</template>
