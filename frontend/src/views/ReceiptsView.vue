<script setup lang="ts">
import { Download, Receipt } from "@lucide/vue";
import { computed } from "vue";
import { downloadReceipt, getFees } from "@/api/portal";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { useDownload } from "@/composables/useDownload";
import { useResource } from "@/composables/useResource";
import { t, tv } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { formatDate, formatINR } from "@/utils/format";

const { session } = useSession();
const fees = useResource(() => getFees(studentArg()), { watch: [() => session.activeStudent] });
const { busyKey, download } = useDownload();
const payments = computed(() => fees.data.value?.payments ?? []);
</script>

<template>
	<div>
		<PageHeader :title="t('receipts.title')" :subtitle="t('receipts.subtitle')" :back="{ name: 'fees' }" />

		<SkeletonList v-if="fees.loading.value && !fees.data.value" />
		<ErrorState v-else-if="fees.error.value" :error="fees.error.value" @retry="fees.reload" />
		<EmptyState v-else-if="!payments.length" :icon="Receipt" :title="t('receipts.empty')" :body="t('receipts.emptyHint')" />

		<ul v-else class="space-y-3">
			<li v-for="payment in payments" :key="payment.name" class="card flex flex-wrap items-center justify-between gap-3">
				<div class="min-w-0">
					<p class="text-lg font-bold text-ink">{{ formatINR(payment.amount, "always") }}</p>
					<p class="text-sm text-muted">{{ formatDate(payment.payment_date) }} · {{ tv("paymentModes", payment.mode_of_payment) }}</p>
					<p class="text-xs text-muted">
						{{ t("receipts.number", { name: payment.name }) }}
						<template v-if="payment.reference_no"> · {{ t("receipts.reference", { reference: payment.reference_no }) }}</template>
					</p>
				</div>
				<button
					type="button"
					class="btn-secondary min-h-11"
					:disabled="busyKey === payment.name"
					:aria-label="t('receipts.downloadLabel', { name: payment.name })"
					@click="download(payment.name, () => downloadReceipt(payment.name))"
				>
					<Download :size="18" aria-hidden="true" />
					{{ busyKey === payment.name ? t("common.downloading") : t("receipts.download") }}
				</button>
			</li>
		</ul>
	</div>
</template>
