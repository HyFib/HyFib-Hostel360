<script setup lang="ts">
import { UtensilsCrossed } from "@lucide/vue";
import { computed } from "vue";
import { getMessBills } from "@/api/portal";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import StatusChip from "@/components/StatusChip.vue";
import { useResource } from "@/composables/useResource";
import { t } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { formatDate, formatDateRange, formatINR, toNumber } from "@/utils/format";

const { session, paymentEnabled } = useSession();
const bills = useResource(() => getMessBills(studentArg()), { watch: [() => session.activeStudent] });
const list = computed(() => bills.data.value ?? []);
</script>

<template>
	<div>
		<PageHeader :title="t('messBills.title')" :subtitle="t('messBills.subtitle')" />

		<SkeletonList v-if="bills.loading.value && !bills.data.value" />
		<ErrorState v-else-if="bills.error.value" :error="bills.error.value" @retry="bills.reload" />
		<EmptyState v-else-if="!list.length" :icon="UtensilsCrossed" :title="t('messBills.empty')" :body="t('messBills.emptyHint')" />

		<ul v-else class="space-y-3">
			<li v-for="bill in list" :key="bill.name" class="card">
				<div class="flex flex-wrap items-start justify-between gap-2">
					<div>
						<p class="font-semibold text-ink">{{ formatDateRange(bill.from_date, bill.to_date) }}</p>
						<p class="text-xs text-muted">{{ bill.name }}</p>
					</div>
					<StatusChip :status="bill.invoice_status" />
				</div>
				<dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
					<div v-if="bill.enrolled_days !== undefined">
						<dt class="text-muted">{{ t("messBills.enrolledDays") }}</dt>
						<dd class="font-medium text-ink">{{ bill.enrolled_days }}</dd>
					</div>
					<div v-if="bill.reduction_days !== undefined">
						<dt class="text-muted">{{ t("messBills.reductionDays") }}</dt>
						<dd class="font-medium text-ink">{{ bill.reduction_days }}</dd>
					</div>
					<div>
						<dt class="text-muted">{{ t("messBills.chargeableDays") }}</dt>
						<dd class="font-medium text-ink">{{ bill.chargeable_days }}</dd>
					</div>
					<div>
						<dt class="text-muted">{{ t("messBills.dailyRate") }}</dt>
						<dd class="font-medium text-ink">{{ formatINR(bill.daily_rate, "always") }}</dd>
					</div>
					<div>
						<dt class="text-muted">{{ t("messBills.messCharge") }}</dt>
						<dd class="font-medium text-ink">{{ formatINR(bill.mess_charge, "always") }}</dd>
					</div>
					<div>
						<dt class="text-muted">{{ t("messBills.extras") }}</dt>
						<dd class="font-medium text-ink">{{ formatINR(bill.extras_amount, "always") }}</dd>
					</div>
				</dl>
				<div class="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
					<div>
						<p class="text-xs text-muted">{{ t("messBills.total") }}</p>
						<p class="text-xl font-bold text-ink">{{ formatINR(bill.total_amount, "always") }}</p>
						<p v-if="bill.due_date && toNumber(bill.outstanding_amount) > 0" class="text-xs text-muted">
							{{ t("fees.dueOn", { date: formatDate(bill.due_date) }) }}
						</p>
					</div>
					<RouterLink
						v-if="bill.fee_invoice && toNumber(bill.outstanding_amount) > 0 && paymentEnabled"
						:to="{ name: 'pay', params: { invoice: bill.fee_invoice } }"
						class="btn-primary"
					>
						{{ t("messBills.pay") }}
					</RouterLink>
				</div>
			</li>
		</ul>
	</div>
</template>
