<script setup lang="ts">
import { CircleAlert, WifiOff } from "@lucide/vue";
import { computed } from "vue";
import type { ApiError } from "@/api/client";
import { t } from "@/i18n";

const props = defineProps<{ error: ApiError | null; compact?: boolean }>();
const emit = defineEmits<{ retry: [] }>();

const offline = computed(() => props.error?.isNetwork ?? false);
</script>

<template>
	<div
		v-if="error"
		role="alert"
		class="card flex items-start gap-3"
		:class="compact ? 'p-3' : 'p-5'"
	>
		<span class="mt-0.5 text-danger" aria-hidden="true">
			<WifiOff v-if="offline" :size="22" />
			<CircleAlert v-else :size="22" />
		</span>
		<div class="min-w-0 flex-1">
			<p class="font-semibold text-ink">{{ offline ? t("errors.offlineTitle") : t("errors.loadFailed") }}</p>
			<p class="mt-0.5 text-sm text-muted">{{ error.message }}</p>
			<button type="button" class="btn-secondary mt-3" @click="emit('retry')">{{ t("common.retry") }}</button>
		</div>
	</div>
</template>
