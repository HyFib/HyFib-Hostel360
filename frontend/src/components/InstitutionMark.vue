<script setup lang="ts">
import { computed, ref } from "vue";

const props = withDefaults(defineProps<{ logo?: string | null; name?: string; size?: number }>(), { size: 36 });
const failed = ref(false);

// Only same-site logos are shown; private files would need a session and break the login page.
const src = computed(() => {
	const logo = props.logo || "";
	if (failed.value || !logo || logo.startsWith("/private/")) return "";
	return logo.startsWith("/") || logo.startsWith("https://") ? logo : "";
});
</script>

<template>
	<img
		v-if="src"
		:src="src"
		:alt="name || ''"
		:width="size"
		:height="size"
		class="shrink-0 rounded-lg bg-white object-contain"
		:style="{ width: `${size}px`, height: `${size}px` }"
		@error="failed = true"
	>
	<span
		v-else
		class="inline-flex shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-[#0b4f9c] to-[#0891b2] font-bold text-white"
		:style="{ width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.42)}px` }"
		aria-hidden="true"
	>H</span>
</template>
