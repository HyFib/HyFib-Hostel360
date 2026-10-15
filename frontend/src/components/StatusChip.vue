<script setup lang="ts">
import { computed } from "vue";
import { tv } from "@/i18n";

const props = defineProps<{ status: string | null | undefined; label?: string }>();

const TONES: Record<string, string> = {
	Paid: "success",
	Approved: "success",
	Resolved: "success",
	Closed: "neutral",
	Issued: "success",
	Returned: "success",
	Success: "success",
	Validated: "success",
	Published: "success",
	Unpaid: "warning",
	"Partly Paid": "warning",
	Pending: "warning",
	"Pending Parent Approval": "warning",
	"Pending Warden Approval": "warning",
	Open: "info",
	"In Progress": "info",
	Reopened: "info",
	"Checked Out": "info",
	Initiated: "info",
	Overdue: "danger",
	Rejected: "danger",
	Escalated: "danger",
	Failed: "danger",
	Expired: "neutral",
	Cancelled: "neutral",
	Draft: "neutral",
	"Not Required": "neutral",
};

const tone = computed(() => TONES[props.status || ""] ?? "neutral");
const text = computed(() => props.label || tv("status", props.status));
</script>

<template>
	<span v-if="status" class="chip" :class="`tone-${tone}`">{{ text }}</span>
</template>
