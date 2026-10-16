<script setup lang="ts">
/**
 * The strip every page of a demonstration site carries. It cannot be dismissed: whoever
 * looks at the screen, at any moment, must be able to tell that nothing here is real.
 */
import { FlaskConical } from "@lucide/vue";
import { computed } from "vue";
import { t } from "@/i18n";
import { useSession } from "@/stores/session";
import { DEMO_INFO_URL, isDemoSite } from "@/utils/demo";

const { session } = useSession();
const shown = computed(() => isDemoSite(session.settings));
</script>

<template>
	<div
		v-if="shown"
		class="flex flex-wrap items-center justify-center gap-x-2 gap-y-0.5 border-b border-warning bg-warning-soft px-4 py-1.5 text-center text-sm font-medium text-ink"
		role="note"
		data-test="demo-banner"
	>
		<FlaskConical :size="16" class="shrink-0 text-warning" aria-hidden="true" />
		<span>{{ t("demo.banner") }}</span>
		<a :href="DEMO_INFO_URL" class="font-semibold text-ink underline">{{ t("demo.about") }}</a>
	</div>
</template>
