<script setup lang="ts">
import { FileText, Receipt, Wallet } from "@lucide/vue";
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { downloadFeeStructure, getFees } from "@/api/portal";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import InvoiceList from "@/components/InvoiceList.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { useDownload } from "@/composables/useDownload";
import { useResource } from "@/composables/useResource";
import { t } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { formatDate, formatINR, toNumber } from "@/utils/format";

const router = useRouter();
const { session, paymentEnabled } = useSession();
const fees = useResource(() => getFees(studentArg()), { watch: [() => session.activeStudent] });
const { busyKey, download } = useDownload();
const selected = ref<string[]>([]);

const summary = computed(() => fees.data.value);
const canPayOnline = computed(() => paymentEnabled.value && summary.value?.payment_enabled !== false);
const openInvoices = computed(() => (summary.value?.invoices ?? []).filter((invoice) => toNumber(invoice.outstanding_amount) > 0));
const settledInvoices = computed(() => (summary.value?.invoices ?? []).filter((invoice) => toNumber(invoice.outstanding_amount) <= 0));

watch(() => session.activeStudent, () => {
	selected.value = [];
});

function pay(invoices: string[]) {
	if (!invoices.length) return;
	void router.push({ name: "pay", query: { invoices: invoices.join(",") } });
}
</script>

<template>
	<div>
		<PageHeader :title="t('fees.title')">
			<template #actions>
				<RouterLink :to="{ name: 'receipts' }" class="btn-secondary min-h-10 px-3 text-sm">
					<Receipt :size="18" aria-hidden="true" />{{ t("fees.receipts") }}
				</RouterLink>
			</template>
		</PageHeader>

		<SkeletonList v-if="fees.loading.value && !summary" card :rows="2" />
		<ErrorState v-else-if="fees.error.value" :error="fees.error.value" @retry="fees.reload" />

		<template v-else-if="summary">
			<section class="mb-5 grid grid-cols-2 gap-3" :aria-label="t('fees.summaryLabel')">
				<div class="card">
					<p class="text-xs font-medium text-muted">{{ t("fees.outstanding") }}</p>
					<p class="mt-1 text-2xl font-bold text-ink">{{ formatINR(summary.outstanding) }}</p>
					<p v-if="summary.next_due_date" class="mt-0.5 text-xs text-muted">{{ t("fees.nextDue", { date: formatDate(summary.next_due_date) }) }}</p>
				</div>
				<div class="card" :class="toNumber(summary.overdue) > 0 ? 'border-danger' : ''">
					<p class="text-xs font-medium text-muted">{{ t("fees.overdue") }}</p>
					<p class="mt-1 text-2xl font-bold" :class="toNumber(summary.overdue) > 0 ? 'text-danger' : 'text-ink'">{{ formatINR(summary.overdue) }}</p>
				</div>
			</section>

			<p v-if="openInvoices.length && !canPayOnline" class="card mb-4 bg-info-soft text-sm text-info" role="status">
				{{ t("fees.paymentDisabled") }}
			</p>

			<section v-if="openInvoices.length" aria-labelledby="fees-open" class="mb-6">
				<h2 id="fees-open" class="section-title mb-1">{{ t("fees.dueInvoices") }}</h2>
				<p v-if="canPayOnline" class="mb-3 text-sm text-muted">{{ t("fees.selectToPay") }}</p>
				<InvoiceList v-model="selected" :invoices="openInvoices" :payable="canPayOnline" @pay="pay" />
			</section>

			<EmptyState v-else :icon="Wallet" :title="t('fees.noDues')" :body="t('fees.noDuesHint')" class="mb-6" />

			<section v-if="settledInvoices.length" aria-labelledby="fees-settled" class="mb-6">
				<h2 id="fees-settled" class="section-title mb-3">{{ t("fees.paidInvoices") }}</h2>
				<InvoiceList :invoices="settledInvoices" />
			</section>

			<button
				type="button"
				class="btn-secondary btn-block"
				:disabled="busyKey === 'structure'"
				@click="download('structure', () => downloadFeeStructure(studentArg()))"
			>
				<FileText :size="18" aria-hidden="true" />
				{{ busyKey === "structure" ? t("common.downloading") : t("fees.feeStructure") }}
			</button>
		</template>
	</div>
</template>
