<script setup lang="ts">
import { t } from "@/i18n";

withDefaults(defineProps<{ rows?: number; card?: boolean }>(), { rows: 3, card: false });
</script>

<template>
	<div role="status" :aria-label="t('common.loading')" class="space-y-3">
		<div v-if="card" class="card space-y-3">
			<div class="skeleton h-4 w-1/3" />
			<div class="skeleton h-8 w-1/2" />
			<div class="skeleton h-4 w-2/3" />
		</div>
		<div v-for="row in rows" :key="row" class="card flex items-center gap-3">
			<div class="skeleton size-10 shrink-0 rounded-full" />
			<div class="flex-1 space-y-2">
				<div class="skeleton h-4 w-3/5" />
				<div class="skeleton h-3 w-2/5" />
			</div>
		</div>
		<span class="sr-only">{{ t("common.loading") }}</span>
	</div>
</template>
