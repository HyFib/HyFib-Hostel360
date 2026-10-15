<script setup lang="ts">
/**
 * Pay selected invoices through the gateway the institution chose.
 *
 * The server creates the order and fixes the amount. With Razorpay the browser opens
 * Checkout in the page and hands the signed response back to verify_payment; with PayU the
 * browser posts the server-signed form to PayU's page and comes back to the fees page, which
 * shows the outcome. A payment the browser could not confirm is still picked up by the
 * server's reconciliation, so the page says so instead of failing.
 */
import { CircleAlert, FlaskConical, Lock } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { ApiError, errorMessage } from "@/api/client";
import { createPayment, getFees, verifyPayment } from "@/api/portal";
import type { VerifyPaymentResult } from "@/api/types";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import PaymentOutcome from "@/components/PaymentOutcome.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { useResource } from "@/composables/useResource";
import { t, tv } from "@/i18n";
import { refreshUnread } from "@/stores/inbox";
import { studentArg, useSession } from "@/stores/session";
import { canPay, totalOutstanding } from "@/utils/fees";
import { formatDate, formatINR } from "@/utils/format";
import { openCheckout } from "@/utils/razorpay";
import { submitRedirectCheckout } from "@/utils/redirectCheckout";

type Phase = "review" | "starting" | "checkout" | "redirecting" | "confirming" | "done";

const BRAND_COLOR = "#0B4F9C";
const route = useRoute();
const { session, paymentEnabled } = useSession();
const fees = useResource(() => getFees(studentArg()), { watch: [() => session.activeStudent] });

const phase = ref<Phase>("review");
const problem = ref("");
const result = ref<VerifyPaymentResult | null>(null);
/** The order just created uses the gateway's test credentials. */
const testOrder = ref(false);
/** Test credentials are in use: say so before anyone types card or UPI details. */
const testMode = computed(() => testOrder.value || Boolean(fees.data.value?.payment_test_mode));

const requested = computed<string[]>(() => {
	const fromParam = typeof route.params.invoice === "string" && route.params.invoice ? [route.params.invoice] : [];
	const fromQuery = typeof route.query.invoices === "string" ? route.query.invoices.split(",") : [];
	return [...new Set([...fromParam, ...fromQuery].map((name) => name.trim()).filter(Boolean))];
});

const payable = computed(() => {
	const invoices = (fees.data.value?.invoices ?? []).filter(canPay);
	return requested.value.length ? invoices.filter((invoice) => requested.value.includes(invoice.name)) : invoices;
});
const total = computed(() => totalOutstanding(payable.value));
const onlineEnabled = computed(() => paymentEnabled.value && fees.data.value?.payment_enabled !== false);
/** A checkout for these invoices was opened and not finished; paying again resumes it. */
const resuming = computed(() => payable.value.some((invoice) => invoice.payment_in_progress));

let attempt = 0;

async function startPayment() {
	if (phase.value !== "review" || !payable.value.length) return;
	const current = ++attempt;
	problem.value = "";
	phase.value = "starting";
	let transaction = "";
	let confirming = false;
	try {
		const order = await createPayment(
			payable.value.map((invoice) => invoice.name),
			studentArg(),
		);
		transaction = order.transaction;
		testOrder.value = Boolean(order.test_mode);
		if (order.gateway === "payu") {
			// The payer leaves for PayU's page and returns to the fees page with the outcome.
			phase.value = "redirecting";
			submitRedirectCheckout(order.checkout);
			return;
		}
		if (order.gateway !== "razorpay") throw new Error("unsupported gateway");
		phase.value = "checkout";
		const outcome = await openCheckout(order.checkout, BRAND_COLOR);
		if (outcome.type === "dismissed") {
			// The payer may already have used "Start again" below; nothing more to do then.
			if (current === attempt && phase.value === "checkout") {
				phase.value = "review";
				problem.value = outcome.failure ? t("pay.failedWithReason", { reason: outcome.failure }) : t("pay.cancelled");
			}
			void fees.reload();
			return;
		}
		// A success always gets verified, even if the payer had given up on the window meanwhile.
		confirming = true;
		phase.value = "confirming";
		const verified = await verifyPayment(transaction, outcome.response);
		result.value = { ...verified, test_mode: verified.test_mode ?? (testOrder.value ? 1 : 0) };
		phase.value = "done";
		void refreshUnread(true);
	} catch (error) {
		if (confirming) {
			// Money may have moved; the server reconciles with Razorpay on its own.
			result.value = {
				status: "Pending",
				payment: null,
				receipt_number: null,
				transaction,
				message: t("pay.verifyLater"),
				test_mode: testOrder.value ? 1 : 0,
			};
			phase.value = "done";
			return;
		}
		phase.value = "review";
		problem.value = error instanceof ApiError ? errorMessage(error) : t("pay.checkoutUnavailable");
		void fees.reload();
	}
}

/** Razorpay sometimes fails before its window appears (bad network, blocked pop-up); let the payer start over. */
function abandonCheckout() {
	if (phase.value !== "checkout") return;
	phase.value = "review";
	problem.value = t("pay.checkoutClosed");
	void fees.reload();
}

function tryAgain() {
	result.value = null;
	phase.value = "review";
	void fees.reload();
}

/** Back from the gateway's page with the browser's Back button: the page comes back as it was left. */
function onPageShow(event: PageTransitionEvent) {
	if (!event.persisted || phase.value !== "redirecting") return;
	phase.value = "review";
	problem.value = t("pay.cancelled");
	void fees.reload();
}

onMounted(() => window.addEventListener("pageshow", onPageShow));
onBeforeUnmount(() => window.removeEventListener("pageshow", onPageShow));
</script>

<template>
	<div>
		<PageHeader :title="t('pay.title')" :back="{ name: 'fees' }" />

		<!-- Outcome -->
		<PaymentOutcome v-if="phase === 'done' && result" :result="result" context="pay" @retry="tryAgain" />

		<SkeletonList v-else-if="fees.loading.value && !fees.data.value" card :rows="1" />
		<ErrorState v-else-if="fees.error.value" :error="fees.error.value" @retry="fees.reload" />

		<template v-else-if="!payable.length">
			<p v-if="problem" class="mb-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
				<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ problem }}
			</p>
			<EmptyState :title="t('pay.nothingToPay')" :body="t('pay.nothingToPayHint')">
				<RouterLink :to="{ name: 'fees' }" class="btn-secondary">{{ t("pay.backToFees") }}</RouterLink>
			</EmptyState>
		</template>

		<template v-else>
			<section class="card" aria-labelledby="pay-summary">
				<h2 id="pay-summary" class="text-sm font-semibold text-muted">{{ t("pay.summary") }}</h2>
				<ul class="list-divided mt-2">
					<li v-for="invoice in payable" :key="invoice.name" class="flex items-start justify-between gap-3 py-3">
						<div class="min-w-0">
							<p class="font-medium text-ink">{{ tv("invoiceTypes", invoice.invoice_type) }}</p>
							<p class="text-xs text-muted">
								{{ invoice.name }}<template v-if="invoice.due_date"> · {{ t("fees.dueOn", { date: formatDate(invoice.due_date) }) }}</template>
							</p>
						</div>
						<p class="shrink-0 font-semibold text-ink">{{ formatINR(invoice.outstanding_amount, "always") }}</p>
					</li>
				</ul>
				<div class="mt-2 flex items-center justify-between border-t border-line pt-3">
					<p class="font-semibold text-ink">{{ t("fees.total") }}</p>
					<p class="text-2xl font-bold text-ink">{{ formatINR(total, "always") }}</p>
				</div>
			</section>

			<p v-if="problem" class="mt-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
				<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ problem }}
			</p>

			<p v-if="!onlineEnabled" class="card mt-4 bg-info-soft text-sm text-info" role="status">{{ t("fees.paymentDisabled") }}</p>

			<template v-else>
				<p v-if="testMode" class="mt-4 flex items-start gap-2 rounded-xl bg-info-soft p-3 text-sm font-medium text-info" role="status" data-test="test-mode">
					<FlaskConical :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ t("pay.testMode") }}
				</p>
				<button
					type="button"
					class="btn-primary btn-block mt-5 min-h-12 text-base"
					:disabled="phase !== 'review'"
					@click="startPayment"
				>
					<Lock :size="18" aria-hidden="true" />
					<span v-if="phase === 'starting'">{{ t("pay.preparing") }}</span>
					<span v-else-if="phase === 'checkout'">{{ t("pay.inCheckout") }}</span>
					<span v-else-if="phase === 'redirecting'">{{ t("pay.redirecting") }}</span>
					<span v-else-if="phase === 'confirming'">{{ t("pay.confirming") }}</span>
					<span v-else-if="resuming">{{ t("pay.resumeButton", { amount: formatINR(total, "always") }) }}</span>
					<span v-else>{{ t("pay.payButton", { amount: formatINR(total, "always") }) }}</span>
				</button>
				<p v-if="resuming && phase === 'review'" class="mt-3 text-sm text-muted">{{ t("pay.resumeHint") }}</p>
				<p class="mt-3 flex items-start gap-2 text-xs text-muted">
					<Lock :size="14" class="mt-0.5 shrink-0" aria-hidden="true" />{{ t("pay.secure") }}
				</p>
				<p v-if="phase === 'redirecting'" class="mt-3 rounded-xl bg-info-soft p-3 text-sm font-medium text-info" role="status">
					{{ t("pay.redirectingHint") }}
				</p>
				<p v-if="phase === 'confirming'" class="mt-3 rounded-xl bg-warning-soft p-3 text-sm font-medium text-warning" role="status">
					{{ t("pay.dontClose") }}
				</p>
				<div v-if="phase === 'checkout'" class="mt-3 text-center">
					<p class="text-sm text-muted">{{ t("pay.noWindow") }}</p>
					<button type="button" class="btn-ghost mt-1" @click="abandonCheckout">{{ t("pay.startAgain") }}</button>
				</div>
			</template>
		</template>
	</div>
</template>
