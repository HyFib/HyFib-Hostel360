<script setup lang="ts">
import { WifiOff } from "@lucide/vue";
import { t } from "@/i18n";
import { isOffline } from "@/stores/network";
</script>

<template>
	<div role="status" aria-live="polite">
		<div v-if="isOffline" class="flex items-center gap-2 bg-warning-soft px-4 py-2 text-sm font-medium text-warning">
			<WifiOff :size="18" class="shrink-0" aria-hidden="true" />
			<span>{{ t("network.offline") }}</span>
		</div>
	</div>
</template>
