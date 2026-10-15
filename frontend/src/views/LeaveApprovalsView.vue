<script setup lang="ts">
/** Parent's inbox of leave requests waiting for their decision. */
import { ClipboardCheck } from "@lucide/vue";
import { computed, reactive, ref } from "vue";
import { errorMessage } from "@/api/client";
import { decideLeave, pendingLeaveApprovals } from "@/api/portal";
import type { Leave } from "@/api/types";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { confirm } from "@/composables/useConfirm";
import { useResource } from "@/composables/useResource";
import { t, tc } from "@/i18n";
import { showToast } from "@/stores/toast";
import { formatDateTime, formatMobile, inclusiveDays } from "@/utils/format";

const approvals = useResource(() => pendingLeaveApprovals());
const list = computed(() => approvals.data.value ?? []);
const remarks = reactive<Record<string, string>>({});
const remarkErrors = reactive<Record<string, string>>({});
const deciding = ref<string | null>(null);

async function decide(leave: Leave, action: "approve" | "reject") {
	const note = (remarks[leave.name] || "").trim();
	delete remarkErrors[leave.name];
	if (action === "reject" && !note) {
		remarkErrors[leave.name] = t("approvals.rejectNeedsRemarks");
		document.getElementById(`remarks-${leave.name}`)?.focus();
		return;
	}
	const ok = await confirm({
		title: action === "approve" ? t("approvals.confirmApproveTitle") : t("approvals.confirmRejectTitle"),
		message: t(action === "approve" ? "approvals.confirmApprove" : "approvals.confirmReject", {
			name: leave.student_name || leave.student || "",
			from: formatDateTime(leave.from_datetime),
		}),
		confirmLabel: action === "approve" ? t("approvals.approve") : t("approvals.reject"),
		danger: action === "reject",
	});
	if (!ok) return;
	deciding.value = leave.name;
	try {
		await decideLeave(leave.name, action, note);
		approvals.data.value = list.value.filter((row) => row.name !== leave.name);
		showToast(action === "approve" ? t("approvals.approved") : t("approvals.rejected"));
	} catch (error) {
		showToast(errorMessage(error), { tone: "error" });
	} finally {
		deciding.value = null;
	}
}
</script>

<template>
	<div>
		<PageHeader :title="t('approvals.title')" :subtitle="t('approvals.subtitle')" />

		<SkeletonList v-if="approvals.loading.value && !approvals.data.value" />
		<ErrorState v-else-if="approvals.error.value" :error="approvals.error.value" @retry="approvals.reload" />
		<EmptyState v-else-if="!list.length" :icon="ClipboardCheck" :title="t('approvals.empty')" :body="t('approvals.emptyHint')" />

		<ul v-else class="space-y-4">
			<li v-for="leave in list" :key="leave.name" class="card">
				<p class="text-lg font-semibold text-ink">{{ leave.student_name || leave.student }}</p>
				<dl class="mt-2 space-y-1 text-sm">
					<div class="flex flex-wrap gap-x-2">
						<dt class="text-muted">{{ t("leave.from") }}:</dt>
						<dd class="font-medium text-ink">{{ formatDateTime(leave.from_datetime) }}</dd>
					</div>
					<div class="flex flex-wrap gap-x-2">
						<dt class="text-muted">{{ t("leave.to") }}:</dt>
						<dd class="font-medium text-ink">{{ formatDateTime(leave.to_datetime) }}</dd>
					</div>
					<div class="flex flex-wrap gap-x-2">
						<dt class="text-muted">{{ t("leave.duration") }}:</dt>
						<dd class="font-medium text-ink">{{ tc("common.days", leave.number_of_days || inclusiveDays(leave.from_datetime, leave.to_datetime)) }}</dd>
					</div>
					<div class="flex flex-wrap gap-x-2">
						<dt class="text-muted">{{ t("leave.destination") }}:</dt>
						<dd class="font-medium text-ink">{{ leave.destination }}</dd>
					</div>
					<div v-if="leave.contact_during_leave" class="flex flex-wrap gap-x-2">
						<dt class="text-muted">{{ t("leave.contact") }}:</dt>
						<dd class="font-medium text-ink">{{ formatMobile(leave.contact_during_leave) }}</dd>
					</div>
				</dl>
				<p class="mt-3 rounded-xl bg-surface-2 p-3 text-sm text-ink">{{ leave.reason }}</p>
				<p v-if="leave.apply_mess_reduction" class="mt-2 text-sm text-muted">{{ t("approvals.withReduction") }}</p>

				<div class="mt-4">
					<label :for="`remarks-${leave.name}`" class="field-label">
						{{ t("approvals.remarks") }}
					</label>
					<textarea
						:id="`remarks-${leave.name}`"
						v-model="remarks[leave.name]"
						class="input min-h-20"
						rows="2"
						maxlength="300"
						:aria-invalid="remarkErrors[leave.name] ? 'true' : undefined"
						:aria-describedby="remarkErrors[leave.name] ? `remarks-${leave.name}-error remarks-${leave.name}-hint` : `remarks-${leave.name}-hint`"
					/>
					<p v-if="remarkErrors[leave.name]" :id="`remarks-${leave.name}-error`" class="field-error" role="alert">{{ remarkErrors[leave.name] }}</p>
					<p :id="`remarks-${leave.name}-hint`" class="field-hint">{{ t("approvals.remarksHint") }}</p>
				</div>
				<div class="mt-4 grid grid-cols-2 gap-2">
					<button type="button" class="btn-danger" :disabled="deciding === leave.name" @click="decide(leave, 'reject')">
						{{ t("approvals.reject") }}
					</button>
					<button type="button" class="btn-primary" :disabled="deciding === leave.name" @click="decide(leave, 'approve')">
						{{ t("approvals.approve") }}
					</button>
				</div>
			</li>
		</ul>
	</div>
</template>
