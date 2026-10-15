<script setup lang="ts">
/**
 * Invoices with checkboxes for the ones that can be paid, and the running total of the
 * selection. Used on the Fees page; kept separate so the selection rules are tested alone.
 */
import { ChevronDown } from "@lucide/vue";
import { computed, ref } from "vue";
import type { Invoice } from "@/api/types";
import { t, tv } from "@/i18n";
import { canPay, totalOutstanding } from "@/utils/fees";
import { formatDate, formatINR } from "@/utils/format";
import StatusChip from "./StatusChip.vue";

const props = defineProps<{ invoices: Invoice[]; payable?: boolean }>();
const selected = defineModel<string[]>({ default: () => [] });
const emit = defineEmits<{ pay: [invoices: string[]] }>();

const expanded = ref<string | null>(null);

const payableInvoices = computed(() => props.invoices.filter(canPay));
const total = computed(() =>
	totalOutstanding(props.invoices.filter((invoice) => selected.value.includes(invoice.name))),
);
const allSelected = computed(
	() =>
		payableInvoices.value.length > 0 && payableInvoices.value.every((invoice) => selected.value.includes(invoice.name)),
);

function toggle(invoice: Invoice) {
	if (!canPay(invoice)) return;
	selected.value = selected.value.includes(invoice.name)
		? selected.value.filter((name) => name !== invoice.name)
		: [...selected.value, invoice.name];
}

function toggleAll() {
	selected.value = allSelected.value ? [] : payableInvoices.value.map((invoice) => invoice.name);
}

defineExpose({ total });
</script>

<template>
	<div>
		<div v-if="payable && payableInvoices.length > 1" class="mb-2 flex justify-end">
			<button type="button" class="btn-ghost min-h-10 px-3 text-sm" data-test="select-all" @click="toggleAll">
				{{ allSelected ? t("fees.clearSelection") : t("fees.selectAll") }}
			</button>
		</div>
		<ul class="space-y-3">
			<li v-for="invoice in invoices" :key="invoice.name" class="card p-0" :data-invoice="invoice.name">
				<div class="flex items-start gap-3 p-4">
					<input
						v-if="payable && canPay(invoice)"
						:id="`pay-${invoice.name}`"
						type="checkbox"
						class="mt-1 size-6 shrink-0 cursor-pointer accent-[var(--h-brand)]"
						:checked="selected.includes(invoice.name)"
						:aria-label="t('fees.select', { name: invoice.name, amount: formatINR(invoice.outstanding_amount) })"
						@change="toggle(invoice)"
					/>
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<p class="font-semibold text-ink">{{ tv("invoiceTypes", invoice.invoice_type) }}</p>
							<StatusChip
								:status="invoice.payment_in_progress ? 'Initiated' : invoice.status"
								:label="invoice.payment_in_progress ? t('fees.paymentInProgress') : undefined"
							/>
						</div>
						<p class="text-xs text-muted">
							{{ invoice.name }} · {{ t("fees.issuedOn", { date: formatDate(invoice.posting_date) }) }}
						</p>
						<div class="mt-2 flex flex-wrap items-end justify-between gap-2">
							<div>
								<p class="text-lg font-bold text-ink">
									{{
										formatINR(
											invoice.outstanding_amount > 0 ? invoice.outstanding_amount : invoice.total_amount,
											"always",
										)
									}}
								</p>
								<p v-if="invoice.outstanding_amount > 0 && invoice.paid_amount > 0" class="text-xs text-muted">
									{{
										t("fees.paidOf", { paid: formatINR(invoice.paid_amount), total: formatINR(invoice.total_amount) })
									}}
								</p>
							</div>
							<p
								v-if="invoice.due_date && invoice.outstanding_amount > 0"
								class="text-sm"
								:class="invoice.status === 'Overdue' ? 'font-semibold text-danger' : 'text-muted'"
							>
								{{ t("fees.dueOn", { date: formatDate(invoice.due_date) }) }}
							</p>
						</div>
						<button
							v-if="invoice.items?.length"
							type="button"
							class="mt-2 -ml-2 inline-flex min-h-10 items-center gap-1 rounded-lg px-2 text-sm font-semibold text-brand-ink"
							:aria-expanded="expanded === invoice.name ? 'true' : 'false'"
							:aria-controls="`items-${invoice.name}`"
							@click="expanded = expanded === invoice.name ? null : invoice.name"
						>
							{{ t("fees.details") }}
							<ChevronDown
								:size="16"
								aria-hidden="true"
								:class="expanded === invoice.name ? 'rotate-180' : ''"
								class="transition-transform"
							/>
						</button>
						<dl
							v-if="expanded === invoice.name"
							:id="`items-${invoice.name}`"
							class="mt-1 space-y-1 rounded-xl bg-surface-2 p-3 text-sm"
						>
							<div v-for="(item, index) in invoice.items" :key="index" class="flex justify-between gap-3">
								<dt class="text-muted">{{ item.description || item.fee_head }}</dt>
								<dd class="font-medium text-ink">{{ formatINR(item.amount, "always") }}</dd>
							</div>
						</dl>
					</div>
				</div>
			</li>
		</ul>

		<div
			v-if="payable && selected.length"
			class="sticky bottom-20 z-20 mt-4 flex items-center justify-between gap-3 rounded-2xl border border-line bg-surface p-3 shadow-lg lg:bottom-4"
		>
			<div aria-live="polite">
				<p class="text-xs text-muted">{{ t("fees.selectedCount", { count: selected.length }) }}</p>
				<p class="text-lg font-bold text-ink" data-test="selected-total">{{ formatINR(total, "always") }}</p>
			</div>
			<button type="button" class="btn-primary" data-test="pay-selected" @click="emit('pay', [...selected])">
				{{ t("fees.payTotal", { amount: formatINR(total) }) }}
			</button>
		</div>
	</div>
</template>
