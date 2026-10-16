<script setup lang="ts">
/**
 * What happened to an online payment: paid (with the receipt), not paid, or still being
 * confirmed. Shown after Razorpay Checkout on the pay page, and on the fees page when a
 * redirect checkout (PayU) brings the payer back.
 */
import { CircleAlert, CircleCheck, Clock, Download, FlaskConical, RefreshCw } from "@lucide/vue";
import { computed } from "vue";
import { downloadReceipt } from "@/api/portal";
import type { VerifyPaymentResult } from "@/api/types";
import { useDownload } from "@/composables/useDownload";
import { t } from "@/i18n";

const props = defineProps<{
	result: VerifyPaymentResult;
	/** "pay": shown on the pay page (links back to fees); "fees": shown on the fees page itself. */
	context?: "pay" | "fees";
	/** A status check is running (the fees page asks again while a payment is being confirmed). */
	checking?: boolean;
}>();
const emit = defineEmits<{ retry: []; dismiss: []; check: [] }>();
const { busyKey, download } = useDownload();

/** Statuses after which the money did not reach the hostel (or was returned). */
const NOT_PAID = new Set(["Failed", "Expired", "Refunded"]);
const outcome = computed<"success" | "failed" | "pending">(() => {
	if (props.result.status === "Success") return "success";
	return NOT_PAID.has(props.result.status) ? "failed" : "pending";
});
const onPayPage = computed(() => (props.context ?? "pay") === "pay");
/**
 * Only this attempt failed (a declined card, a cancelled UPI request): the payment request stays
 * open and paying again continues it. Expired or refunded requests and unmatched replies differ.
 */
const attemptFailed = computed(() => props.result.status === "Failed" && Boolean(props.result.transaction));
/** Paid (or attempted) with the gateway's test credentials. */
const isTest = computed(() => Boolean(Number(props.result.test_mode)));
</script>

<template>
	<section
		class="card p-6 text-center"
		role="status"
		aria-live="polite"
		data-test="payment-outcome"
		:data-outcome="outcome"
	>
		<template v-if="outcome === 'success'">
			<CircleCheck :size="48" class="mx-auto text-success" aria-hidden="true" />
			<h2 class="mt-3 text-xl font-bold text-ink">{{ t("pay.successTitle") }}</h2>
			<p class="mt-1 text-muted">
				{{ t("pay.successBody", { receipt: result.receipt_number || result.payment || "" }) }}
			</p>
			<p
				v-if="isTest"
				class="mt-3 inline-flex items-start gap-1.5 rounded-xl bg-info-soft px-3 py-2 text-left text-sm font-medium text-info"
				data-test="test-outcome"
			>
				<FlaskConical :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />{{ t("pay.testReceipt") }}
			</p>
			<div class="mt-6 grid gap-2 sm:flex sm:justify-center">
				<button
					v-if="result.payment"
					type="button"
					class="btn-primary"
					:disabled="busyKey === 'receipt'"
					@click="download('receipt', () => downloadReceipt(result.payment!))"
				>
					<Download :size="18" aria-hidden="true" />
					{{ busyKey === "receipt" ? t("common.downloading") : t("pay.downloadReceipt") }}
				</button>
				<RouterLink v-if="onPayPage" :to="{ name: 'fees' }" class="btn-secondary">{{ t("pay.backToFees") }}</RouterLink>
				<button v-else type="button" class="btn-secondary" @click="emit('dismiss')">{{ t("common.close") }}</button>
			</div>
		</template>
		<template v-else-if="outcome === 'failed'">
			<CircleAlert :size="48" class="mx-auto text-danger" aria-hidden="true" />
			<h2 class="mt-3 text-xl font-bold text-ink">{{ t("pay.failedTitle") }}</h2>
			<p class="mt-1 text-muted">{{ result.message || t("pay.failedBody") }}</p>
			<p v-if="attemptFailed" class="mt-1 text-sm text-muted" data-test="dues-still-open">
				{{ t("pay.duesStillOpen") }}
			</p>
			<p
				v-if="isTest"
				class="mt-3 inline-flex items-start gap-1.5 rounded-xl bg-info-soft px-3 py-2 text-left text-sm font-medium text-info"
				data-test="test-outcome"
			>
				<FlaskConical :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />{{ t("pay.testAttempt") }}
			</p>
			<div class="mt-6 grid gap-2 sm:flex sm:justify-center">
				<button type="button" class="btn-primary" @click="emit('retry')">{{ t("pay.retry") }}</button>
				<RouterLink v-if="onPayPage" :to="{ name: 'fees' }" class="btn-secondary">{{ t("pay.backToFees") }}</RouterLink>
				<button v-else type="button" class="btn-secondary" @click="emit('dismiss')">{{ t("common.close") }}</button>
			</div>
		</template>
		<template v-else>
			<Clock :size="48" class="mx-auto text-warning" aria-hidden="true" />
			<h2 class="mt-3 text-xl font-bold text-ink">{{ t("pay.pendingTitle") }}</h2>
			<p class="mt-1 text-muted">{{ result.message || t("pay.verifyLater") }}</p>
			<p v-if="result.transaction" class="mt-2 text-xs text-muted">
				{{ t("pay.reference", { reference: result.transaction }) }}
			</p>
			<div class="mt-6 grid gap-2 sm:flex sm:justify-center">
				<button v-if="!onPayPage" type="button" class="btn-primary" :disabled="checking" @click="emit('check')">
					<RefreshCw :size="18" aria-hidden="true" :class="checking ? 'motion-safe:animate-spin' : ''" />
					{{ checking ? t("pay.checking") : t("pay.checkAgain") }}
				</button>
				<RouterLink v-if="onPayPage" :to="{ name: 'fees' }" class="btn-secondary">{{ t("pay.backToFees") }}</RouterLink>
				<button v-else type="button" class="btn-secondary" @click="emit('dismiss')">{{ t("common.close") }}</button>
			</div>
		</template>
	</section>
</template>
