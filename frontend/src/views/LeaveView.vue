<script setup lang="ts">
import { CalendarDays, ChevronDown, MapPin, Plus } from "@lucide/vue";
import { computed, ref } from "vue";
import { errorMessage } from "@/api/client";
import { cancelLeave, listLeaves } from "@/api/portal";
import type { Leave } from "@/api/types";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import StatusChip from "@/components/StatusChip.vue";
import StepTimeline from "@/components/StepTimeline.vue";
import { confirm } from "@/composables/useConfirm";
import { useResource } from "@/composables/useResource";
import { t, tc } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";
import { formatDateTime, inclusiveDays } from "@/utils/format";
import { leaveSteps } from "@/utils/leave";

const { session, isParent } = useSession();
const leaves = useResource(() => listLeaves(studentArg()), { watch: [() => session.activeStudent] });
const list = computed(() => leaves.data.value ?? []);
const open = ref<string | null>(null);
const cancelling = ref<string | null>(null);

async function cancel(leave: Leave) {
	const ok = await confirm({
		title: t("leave.cancelTitle"),
		message: t("leave.cancelConfirm", { from: formatDateTime(leave.from_datetime) }),
		confirmLabel: t("leave.cancel"),
		cancelLabel: t("common.keep"),
		danger: true,
	});
	if (!ok) return;
	cancelling.value = leave.name;
	const before = list.value;
	leaves.data.value = before.map((row) => (row.name === leave.name ? { ...row, status: "Cancelled", can_cancel: false } : row));
	try {
		await cancelLeave(leave.name);
		showToast(t("leave.cancelled"));
	} catch (error) {
		leaves.data.value = before;
		showToast(errorMessage(error), { tone: "error" });
	} finally {
		cancelling.value = null;
	}
}
</script>

<template>
	<div>
		<PageHeader :title="t('leave.title')" :subtitle="t('leave.subtitle')">
			<template #actions>
				<RouterLink v-if="isParent" :to="{ name: 'leave-approvals' }" class="btn-secondary min-h-10 px-3 text-sm">{{ t("nav.approvals") }}</RouterLink>
				<RouterLink :to="{ name: 'leave-new' }" class="btn-primary min-h-10 px-3 text-sm">
					<Plus :size="18" aria-hidden="true" />{{ t("leave.apply") }}
				</RouterLink>
			</template>
		</PageHeader>

		<SkeletonList v-if="leaves.loading.value && !leaves.data.value" />
		<ErrorState v-else-if="leaves.error.value" :error="leaves.error.value" @retry="leaves.reload" />
		<EmptyState v-else-if="!list.length" :icon="CalendarDays" :title="t('leave.empty')" :body="t('leave.emptyHint')">
			<RouterLink :to="{ name: 'leave-new' }" class="btn-primary">{{ t("leave.apply") }}</RouterLink>
		</EmptyState>

		<ul v-else class="space-y-3">
			<li v-for="leave in list" :key="leave.name" class="card p-0">
				<button
					type="button"
					class="flex w-full items-start gap-3 p-4 text-left"
					:aria-expanded="open === leave.name ? 'true' : 'false'"
					:aria-controls="`leave-${leave.name}`"
					@click="open = open === leave.name ? null : leave.name"
				>
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<p class="flex items-center gap-1.5 font-semibold text-ink">
								<MapPin :size="16" class="shrink-0 text-muted" aria-hidden="true" />{{ leave.destination }}
							</p>
							<StatusChip :status="leave.status" />
						</div>
						<p class="mt-1 text-sm text-muted">
							{{ formatDateTime(leave.from_datetime) }} → {{ formatDateTime(leave.to_datetime) }}
						</p>
						<p class="text-xs text-muted">{{ tc("common.days", leave.number_of_days || inclusiveDays(leave.from_datetime, leave.to_datetime)) }} · {{ leave.name }}</p>
					</div>
					<ChevronDown :size="20" class="mt-1 shrink-0 text-muted transition-transform" :class="open === leave.name ? 'rotate-180' : ''" aria-hidden="true" />
				</button>
				<div v-if="open === leave.name" :id="`leave-${leave.name}`" class="border-t border-line p-4">
					<p class="mb-4 text-sm text-ink"><span class="font-semibold">{{ t("leave.reason") }}:</span> {{ leave.reason }}</p>
					<StepTimeline :steps="leaveSteps(leave)" :label="t('leave.progress')" />
					<p v-if="leave.mess_reduction" class="mt-4 text-sm text-muted">{{ t("leave.reductionLinked", { name: leave.mess_reduction }) }}</p>
					<button
						v-if="leave.can_cancel"
						type="button"
						class="btn-danger mt-4"
						:disabled="cancelling === leave.name"
						@click="cancel(leave)"
					>
						{{ t("leave.cancel") }}
					</button>
				</div>
			</li>
		</ul>
	</div>
</template>
