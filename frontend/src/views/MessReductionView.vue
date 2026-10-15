<script setup lang="ts">
import { CalendarOff, Plus } from "@lucide/vue";
import { computed, ref } from "vue";
import { errorMessage } from "@/api/client";
import { cancelMessReduction, listMessReductions } from "@/api/portal";
import type { MessReduction } from "@/api/types";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import StatusChip from "@/components/StatusChip.vue";
import { confirm } from "@/composables/useConfirm";
import { useResource } from "@/composables/useResource";
import { t, tc, tv } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";
import { formatDateRange, formatDateTime } from "@/utils/format";

const { session, reductionMinDays } = useSession();
const reductions = useResource(() => listMessReductions(studentArg()), { watch: [() => session.activeStudent] });
const list = computed(() => reductions.data.value ?? []);
const cancelling = ref<string | null>(null);

async function cancel(item: MessReduction) {
	const ok = await confirm({
		title: t("reduction.cancelTitle"),
		message: t("reduction.cancelConfirm", { period: formatDateRange(item.from_date, item.to_date) }),
		confirmLabel: t("reduction.cancel"),
		cancelLabel: t("common.keep"),
		danger: true,
	});
	if (!ok) return;
	cancelling.value = item.name;
	// Show the change straight away; put it back if the server refuses.
	const previous = { status: item.status, can_cancel: item.can_cancel };
	reductions.data.value = list.value.map((row) => (row.name === item.name ? { ...row, status: "Cancelled", can_cancel: false } : row));
	try {
		await cancelMessReduction(item.name);
		showToast(t("reduction.cancelled"));
	} catch (error) {
		reductions.data.value = list.value.map((row) => (row.name === item.name ? { ...row, ...previous } : row));
		showToast(errorMessage(error), { tone: "error" });
	} finally {
		cancelling.value = null;
	}
}
</script>

<template>
	<div>
		<PageHeader :title="t('reduction.title')" :subtitle="t('reduction.subtitle', { days: reductionMinDays })">
			<template #actions>
				<RouterLink :to="{ name: 'mess-reduction-new' }" class="btn-primary min-h-10 px-3 text-sm">
					<Plus :size="18" aria-hidden="true" />{{ t("reduction.apply") }}
				</RouterLink>
			</template>
		</PageHeader>

		<SkeletonList v-if="reductions.loading.value && !reductions.data.value" />
		<ErrorState v-else-if="reductions.error.value" :error="reductions.error.value" @retry="reductions.reload" />
		<EmptyState v-else-if="!list.length" :icon="CalendarOff" :title="t('reduction.empty')" :body="t('reduction.emptyHint', { days: reductionMinDays })">
			<RouterLink :to="{ name: 'mess-reduction-new' }" class="btn-primary">{{ t("reduction.apply") }}</RouterLink>
		</EmptyState>

		<ul v-else class="space-y-3">
			<li v-for="item in list" :key="item.name" class="card">
				<div class="flex flex-wrap items-start justify-between gap-2">
					<div>
						<p class="font-semibold text-ink">{{ formatDateRange(item.from_date, item.to_date) }}</p>
						<p class="text-sm text-muted">{{ tc("common.days", item.number_of_days) }} · {{ tv("reductionReasons", item.reason) }}</p>
					</div>
					<StatusChip :status="item.status" />
				</div>
				<p v-if="item.description" class="mt-2 text-sm text-ink">{{ item.description }}</p>
				<p v-if="item.approval_remarks" class="mt-2 rounded-xl bg-surface-2 p-3 text-sm text-ink">
					<span class="font-semibold">{{ t("reduction.remarks") }}:</span> {{ item.approval_remarks }}
				</p>
				<div class="mt-3 flex flex-wrap items-center justify-between gap-2">
					<p class="text-xs text-muted">
						{{ item.name }}<template v-if="item.decided_on"> · {{ t("reduction.decidedOn", { date: formatDateTime(item.decided_on) }) }}</template>
					</p>
					<button
						v-if="item.can_cancel"
						type="button"
						class="btn-danger min-h-10 px-3 text-sm"
						:disabled="cancelling === item.name"
						@click="cancel(item)"
					>
						{{ t("reduction.cancel") }}
					</button>
				</div>
			</li>
		</ul>
	</div>
</template>
