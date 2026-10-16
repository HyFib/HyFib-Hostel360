<script setup lang="ts">
import type { Component } from "vue";

defineProps<{
	title: string;
	body?: string;
	icon?: Component;
}>();
</script>

<template>
	<div class="card flex flex-col items-center px-6 py-10 text-center">
		<span
			v-if="icon"
			class="mb-3 inline-flex size-14 items-center justify-center rounded-full bg-brand-soft text-brand-ink"
			aria-hidden="true"
		>
			<component :is="icon" :size="26" />
		</span>
		<p class="text-base font-semibold text-ink">{{ title }}</p>
		<p v-if="body" class="mt-1 max-w-sm text-sm text-muted">{{ body }}</p>
		<div v-if="$slots.default" class="mt-5 flex flex-wrap justify-center gap-2">
			<slot />
		</div>
	</div>
</template>
