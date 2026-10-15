<script setup lang="ts">
/**
 * Dues, invoices and the fee structure. A redirect checkout (PayU) brings the payer back here
 * with ?payment=<transaction>&status=…; the status in the address is only a hint, so the
 * outcome shown is the one the server confirms for that transaction.
 */
import { FileText, FlaskConical, Receipt, Wallet } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "@/api/client";
import { downloadFeeStructure, getFees, getPaymentStatus } from "@/api/portal";
import type { VerifyPaymentResult } from "@/api/types";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import InvoiceList from "@/components/InvoiceList.vue";
import PageHeader from "@/components/PageHeader.vue";
import PaymentOutcome from "@/components/PaymentOutcome.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { useDownload } from "@/composables/useDownload";
import { useResource } from "@/composables/useResource";
import { t } from "@/i18n";
import { refreshUnread } from "@/stores/inbox";
import { studentArg, useSession } from "@/stores/session";
import { formatDate, formatINR, toNumber } from "@/utils/format";

/** How long to wait before asking once more about a payment the gateway is still confirming. */
const RECHECK_DELAY_MS = 5000;
const TRANSACTION_NAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/;

const route = useRoute();
const router = useRouter();
const { session, paymentEnabled } = useSession();
const fees = useResource(() => getFees(studentArg()), { watch: [() => session.activeStudent] });
const { busyKey, download } = useDownload();
const selected = ref<string[]>([]);

const returned = ref<VerifyPaymentResult | null>(null);
const checking = ref(false);
let recheckTimer: ReturnType<typeof setTimeout> | null = null;
let autoRechecked = false;

async function checkPayment(transaction: string) {
	if (recheckTimer) clearTimeout(recheckTimer);
	checking.value = true;
	try {
		returned.value = await getPaymentStatus(transaction);
	} catch (error) {
		// Not one of this user's payments: say the reply could not be matched. Anything else
		// (no connection, server trouble) leaves the payment to be confirmed later.
		const unmatched = error instanceof ApiError && (error.kind === "not_found" || error.kind === "permission");
		returned.value = unmatched
			? { status: "Failed", payment: null, receipt_number: null, message: t("pay.returnUnmatched") }
			: { status: "Pending", payment: null, receipt_number: null, transaction, message: t("pay.verifyLater") };
	} finally {
		checking.value = false;
	}
	void fees.reload();
	if (returned.value?.status === "Success") void refreshUnread(true);
	else if (returned.value && !["Failed", "Expired", "Refunded"].includes(returned.value.status) && !autoRechecked) {
		autoRechecked = true;
		recheckTimer = setTimeout(() => void checkPayment(transaction), RECHECK_DELAY_MS);
	}
}

function readPaymentReturn() {
	const transaction = typeof route.query.payment === "string" ? route.query.payment : "";
	const status = typeof route.query.status === "string" ? route.query.status : "";
	if (!transaction && !status) return;
	// Drop the parameters so a reload or a shared link never replays the outcome.
	void router.replace({ query: {} });
	if (TRANSACTION_NAME.test(transaction)) {
		void checkPayment(transaction);
	} else {
		// The gateway's reply could not be matched to a payment of ours.
		returned.value = { status: "Failed", payment: null, receipt_number: null, message: t("pay.returnUnmatched") };
	}
}

function dismissOutcome() {
	if (recheckTimer) clearTimeout(recheckTimer);
	returned.value = null;
}

onMounted(readPaymentReturn);
onBeforeUnmount(() => {
	if (recheckTimer) clearTimeout(recheckTimer);
});

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

		<PaymentOutcome
			v-if="returned"
			class="mb-5"
			:result="returned"
			context="fees"
			:checking="checking"
			@retry="dismissOutcome"
			@dismiss="dismissOutcome"
			@check="returned.transaction && checkPayment(returned.transaction)"
		/>

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
				<p v-if="canPayOnline && summary.payment_test_mode" class="mb-3 flex items-start gap-1.5 rounded-xl bg-info-soft px-3 py-2 text-sm font-medium text-info" data-test="test-mode">
					<FlaskConical :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />{{ t("fees.testMode") }}
				</p>
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
