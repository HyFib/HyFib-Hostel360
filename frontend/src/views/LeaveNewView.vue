<script setup lang="ts">
import { useRouter } from "vue-router";
import { applyLeave } from "@/api/portal";
import type { LeaveInput } from "@/api/types";
import LeaveForm from "@/components/LeaveForm.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useSubmit } from "@/composables/useSubmit";
import { t, tv } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";

const router = useRouter();
const { isParent, activeWard, reductionMinDays, messDayCutoffHour, siteTimeZone } = useSession();
const { busy, error, run } = useSubmit();

async function submit(input: LeaveInput) {
	const result = await run(() => applyLeave(input, studentArg()));
	if (result) {
		showToast(
			result.status ? t("leave.submittedWithStatus", { status: tv("status", result.status) }) : t("leave.submitted"),
		);
		await router.push({ name: "leave" });
	}
}
</script>

<template>
	<div>
		<PageHeader
			:title="t('leave.new')"
			:subtitle="isParent && activeWard ? t('leave.onBehalf', { name: activeWard.full_name }) : t('leave.newSubtitle')"
			:back="{ name: 'leave' }"
		/>
		<LeaveForm
			:busy="busy"
			:server-error="error"
			:reduction-min-days="reductionMinDays"
			:cutoff-hour="messDayCutoffHour"
			:time-zone="siteTimeZone"
			@submit="submit"
		/>
	</div>
</template>
