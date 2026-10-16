<script setup lang="ts">
import { CircleAlert, Clock, FileText, Lock, Send, Star } from "@lucide/vue";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { commentGrievance, getGrievance, rateGrievance, reopenGrievance } from "@/api/portal";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import StarRating from "@/components/StarRating.vue";
import StatusChip from "@/components/StatusChip.vue";
import { useResource } from "@/composables/useResource";
import { useSubmit } from "@/composables/useSubmit";
import { t, tv } from "@/i18n";
import { showToast } from "@/stores/toast";
import { formatBytes, formatDate, formatDateTime } from "@/utils/format";

const route = useRoute();
const name = computed(() => String(route.params.name || ""));
const grievance = useResource(() => getGrievance(name.value), { watch: [name] });
const item = computed(() => grievance.data.value);

const comment = ref("");
const commentAction = useSubmit();
const reopenReason = ref("");
const showReopen = ref(false);
const reopenAction = useSubmit();
const rating = ref(0);
const ratingFeedback = ref("");
const ratingError = ref("");
const rateAction = useSubmit();

const timeline = computed(() => [...(item.value?.timeline ?? [])].reverse());

async function sendComment() {
	const text = comment.value.trim();
	if (!text) return;
	const ok = await commentAction.run(() => commentGrievance(name.value, text));
	if (ok) {
		comment.value = "";
		showToast(t("grievances.commentAdded"));
		await grievance.reload();
	}
}

async function reopen() {
	const reason = reopenReason.value.trim();
	if (!reason) {
		reopenAction.error.value = t("grievances.reopenNeedsReason");
		return;
	}
	const ok = await reopenAction.run(() => reopenGrievance(name.value, reason));
	if (ok) {
		showReopen.value = false;
		reopenReason.value = "";
		showToast(t("grievances.reopened"));
		await grievance.reload();
	}
}

async function rate() {
	ratingError.value = "";
	if (!rating.value) {
		ratingError.value = t("grievances.ratingRequired");
		return;
	}
	const ok = await rateAction.run(() => rateGrievance(name.value, rating.value, ratingFeedback.value.trim()));
	if (ok) {
		showToast(t("grievances.rated"));
		await grievance.reload();
	}
}
</script>

<template>
	<div>
		<PageHeader :title="item?.subject || t('grievances.detailTitle')" :back="{ name: 'grievances' }" />

		<SkeletonList v-if="grievance.loading.value && !item" card :rows="2" />
		<ErrorState v-else-if="grievance.error.value" :error="grievance.error.value" @retry="grievance.reload" />

		<template v-else-if="item">
			<section class="card">
				<div class="flex flex-wrap items-center gap-2">
					<StatusChip :status="item.status" />
					<span class="chip tone-neutral">{{ tv("grievanceCategories", item.category) }}</span>
					<span
						class="chip"
						:class="item.priority === 'Urgent' || item.priority === 'High' ? 'tone-danger' : 'tone-neutral'"
						>{{ tv("priorities", item.priority) }}</span
					>
					<span v-if="item.is_confidential" class="chip tone-info"
						><Lock :size="12" aria-hidden="true" />{{ t("grievances.confidentialShort") }}</span
					>
				</div>
				<p class="mt-3 whitespace-pre-line text-[0.95rem] text-ink">{{ item.description }}</p>
				<p class="mt-3 text-xs text-muted">
					{{ item.name }} · {{ t("grievances.raisedOn", { date: formatDateTime(item.creation) }) }}
				</p>
				<p
					v-if="item.sla_due_by && !['Resolved', 'Closed'].includes(item.status)"
					class="mt-1 flex items-center gap-1 text-xs text-muted"
				>
					<Clock :size="14" aria-hidden="true" />{{
						t("grievances.expectedBy", { date: formatDateTime(item.sla_due_by) })
					}}
				</p>

				<div v-if="item.attachments?.length" class="mt-4">
					<h2 class="text-sm font-semibold text-muted">{{ t("grievances.attachmentsTitle") }}</h2>
					<ul class="mt-2 space-y-1">
						<li v-for="file in item.attachments" :key="file.name">
							<!-- Served by the portal after the grievance's own checks; the private file URL would not open for a parent. -->
							<a
								:href="file.url"
								target="_blank"
								rel="noopener"
								class="inline-flex min-h-11 items-center gap-2 text-sm"
							>
								<FileText :size="16" aria-hidden="true" />
								{{ file.file_name }}
								<span v-if="file.file_size" class="text-muted">({{ formatBytes(file.file_size) }})</span>
							</a>
						</li>
					</ul>
				</div>
			</section>

			<section v-if="item.resolution" class="card mt-4 border-success" aria-labelledby="g-resolution">
				<h2 id="g-resolution" class="text-sm font-semibold text-success">{{ t("grievances.resolution") }}</h2>
				<p class="mt-1 whitespace-pre-line text-[0.95rem] text-ink">{{ item.resolution }}</p>
				<p v-if="item.resolved_on" class="mt-2 text-xs text-muted">{{ formatDate(item.resolved_on) }}</p>
			</section>

			<!-- Rating -->
			<section v-if="item.student_rating" class="card mt-4" aria-labelledby="g-rated">
				<h2 id="g-rated" class="text-sm font-semibold text-muted">{{ t("grievances.yourRating") }}</h2>
				<p class="mt-1 flex items-center gap-1" :aria-label="t('common.stars', { count: item.student_rating, max: 5 })">
					<Star
						v-for="n in 5"
						:key="n"
						:size="22"
						aria-hidden="true"
						:class="n <= (item.student_rating || 0) ? 'fill-amber-400 text-amber-500' : 'text-muted'"
					/>
				</p>
				<p v-if="item.student_feedback" class="mt-2 text-sm text-ink">{{ item.student_feedback }}</p>
			</section>
			<form v-else-if="item.can_rate" class="card mt-4" novalidate @submit.prevent="rate">
				<h2 class="section-title mb-2">{{ t("grievances.rate") }}</h2>
				<StarRating v-model="rating" :label="t('grievances.rateLabel')" :error="ratingError" />
				<label for="rating-feedback" class="field-label"
					>{{ t("grievances.ratingFeedback") }}
					<span class="font-normal text-muted">{{ t("common.optional") }}</span></label
				>
				<textarea id="rating-feedback" v-model="ratingFeedback" class="input min-h-20" rows="2" maxlength="500" />
				<p v-if="rateAction.error.value" class="field-error" role="alert">{{ rateAction.error.value }}</p>
				<button type="submit" class="btn-primary mt-3" :disabled="rateAction.busy.value">
					{{ t("grievances.submitRating") }}
				</button>
			</form>

			<!-- Timeline -->
			<section class="mt-6" aria-labelledby="g-timeline">
				<h2 id="g-timeline" class="section-title mb-3">{{ t("grievances.timeline") }}</h2>
				<ol v-if="timeline.length" class="space-y-3">
					<li
						v-for="(entry, index) in timeline"
						:key="entry.id || index"
						class="rounded-2xl border p-3"
						:class="entry.is_staff ? 'border-brand-soft bg-brand-soft' : 'border-line bg-surface'"
					>
						<p class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
							<span class="font-semibold text-ink">{{ entry.is_mine ? t("grievances.byYou") : entry.by }}</span>
							<span>{{ formatDateTime(entry.on) }}</span>
						</p>
						<p class="mt-1 whitespace-pre-line text-sm text-ink">{{ entry.display_message || entry.message }}</p>
						<p v-if="entry.status_change" class="mt-2"><StatusChip :status="entry.status_change" /></p>
					</li>
				</ol>
				<p v-else class="muted">{{ t("grievances.noUpdates") }}</p>
			</section>

			<!-- Comment -->
			<form v-if="item.can_comment" class="card mt-4" novalidate @submit.prevent="sendComment">
				<label for="g-comment" class="field-label">{{ t("grievances.addComment") }}</label>
				<textarea
					id="g-comment"
					v-model="comment"
					class="input min-h-20"
					rows="3"
					maxlength="2000"
					:placeholder="t('grievances.commentPlaceholder')"
				/>
				<p v-if="commentAction.error.value" class="field-error" role="alert">{{ commentAction.error.value }}</p>
				<button type="submit" class="btn-primary mt-3" :disabled="commentAction.busy.value || !comment.trim()">
					<Send :size="16" aria-hidden="true" />{{ t("grievances.send") }}
				</button>
			</form>

			<!-- Reopen -->
			<div v-if="item.can_reopen" class="mt-4">
				<button v-if="!showReopen" type="button" class="btn-secondary" @click="showReopen = true">
					{{ t("grievances.reopen") }}
				</button>
				<form v-else class="card" novalidate @submit.prevent="reopen">
					<label for="g-reopen" class="field-label">{{ t("grievances.reopenReason") }}</label>
					<textarea
						id="g-reopen"
						v-model="reopenReason"
						class="input min-h-20"
						rows="3"
						maxlength="1000"
						:aria-invalid="reopenAction.error.value ? 'true' : undefined"
					/>
					<p v-if="reopenAction.error.value" class="field-error" role="alert">
						<CircleAlert :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />{{ reopenAction.error.value }}
					</p>
					<div class="mt-3 flex gap-2">
						<button type="button" class="btn-secondary" @click="showReopen = false">{{ t("common.cancel") }}</button>
						<button type="submit" class="btn-primary" :disabled="reopenAction.busy.value">
							{{ t("grievances.reopen") }}
						</button>
					</div>
				</form>
			</div>
		</template>
	</div>
</template>
