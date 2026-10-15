<script setup lang="ts">
import { ChevronLeft } from "@lucide/vue";
import type { RouteLocationRaw } from "vue-router";
import { t } from "@/i18n";

defineProps<{
	title: string;
	subtitle?: string;
	back?: RouteLocationRaw;
}>();
</script>

<template>
	<header class="mb-5">
		<RouterLink
			v-if="back"
			:to="back"
			class="-ml-2 mb-1 inline-flex min-h-11 items-center gap-1 rounded-lg px-2 text-sm font-medium no-underline"
		>
			<ChevronLeft :size="18" aria-hidden="true" />
			{{ t("common.back") }}
		</RouterLink>
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div class="min-w-0">
				<h1 class="text-2xl font-bold tracking-tight text-ink" tabindex="-1" data-page-title>{{ title }}</h1>
				<p v-if="subtitle" class="mt-1 text-[0.95rem] text-muted">{{ subtitle }}</p>
			</div>
			<div v-if="$slots.actions" class="flex shrink-0 items-center gap-2">
				<slot name="actions" />
			</div>
		</div>
	</header>
</template>
