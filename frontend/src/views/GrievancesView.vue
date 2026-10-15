<script setup lang="ts">
import { ChevronRight, Lock, MessageSquareWarning, Plus, Star } from "@lucide/vue";
import { computed } from "vue";
import { listGrievances } from "@/api/portal";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import StatusChip from "@/components/StatusChip.vue";
import { useResource } from "@/composables/useResource";
import { t, tv } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { formatDate } from "@/utils/format";

const { session } = useSession();
const grievances = useResource(() => listGrievances(studentArg()), { watch: [() => session.activeStudent] });
const list = computed(() => grievances.data.value ?? []);
</script>

<template>
	<div>
		<PageHeader :title="t('grievances.title')" :subtitle="t('grievances.subtitle')">
			<template #actions>
				<RouterLink :to="{ name: 'grievance-new' }" class="btn-primary min-h-10 px-3 text-sm">
					<Plus :size="18" aria-hidden="true" />{{ t("grievances.raise") }}
				</RouterLink>
			</template>
		</PageHeader>

		<SkeletonList v-if="grievances.loading.value && !grievances.data.value" />
		<ErrorState v-else-if="grievances.error.value" :error="grievances.error.value" @retry="grievances.reload" />
		<EmptyState v-else-if="!list.length" :icon="MessageSquareWarning" :title="t('grievances.empty')" :body="t('grievances.emptyHint')">
			<RouterLink :to="{ name: 'grievance-new' }" class="btn-primary">{{ t("grievances.raise") }}</RouterLink>
		</EmptyState>

		<ul v-else class="space-y-3">
			<li v-for="item in list" :key="item.name">
				<RouterLink :to="{ name: 'grievance', params: { name: item.name } }" class="card-link flex items-center gap-3">
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<p class="min-w-0 truncate font-semibold">{{ item.subject }}</p>
							<StatusChip :status="item.status" />
						</div>
						<p class="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-muted">
							<span>{{ tv("grievanceCategories", item.category) }}</span>
							<span aria-hidden="true">·</span>
							<span>{{ formatDate(item.creation) }}</span>
							<span v-if="item.is_confidential" class="inline-flex items-center gap-1">
								<Lock :size="14" aria-hidden="true" />{{ t("grievances.confidentialShort") }}
							</span>
							<span v-if="item.student_rating" class="inline-flex items-center gap-0.5">
								<Star :size="14" class="fill-amber-400 text-amber-500" aria-hidden="true" />
								<span>{{ t("common.stars", { count: item.student_rating, max: 5 }) }}</span>
							</span>
						</p>
						<p class="text-xs text-muted">{{ item.name }}</p>
					</div>
					<ChevronRight :size="18" class="shrink-0 text-muted" aria-hidden="true" />
				</RouterLink>
			</li>
		</ul>
	</div>
</template>
