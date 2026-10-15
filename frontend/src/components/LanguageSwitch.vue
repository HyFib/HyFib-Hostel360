<script setup lang="ts">
import { Languages } from "@lucide/vue";
import { LOCALES, locale, setLocale, t } from "@/i18n";

defineProps<{ compact?: boolean }>();
</script>

<template>
	<div class="flex items-center gap-2">
		<Languages v-if="!compact" :size="18" class="text-muted" aria-hidden="true" />
		<div
			role="group"
			:aria-label="t('profile.language')"
			class="inline-flex rounded-xl border border-line bg-surface p-1"
		>
			<button
				v-for="option in LOCALES"
				:key="option.code"
				type="button"
				:lang="option.code"
				class="min-h-9 rounded-lg px-3 text-sm font-semibold transition-colors"
				:class="locale === option.code ? 'bg-brand text-on-brand' : 'text-ink hover:bg-surface-2'"
				:aria-pressed="locale === option.code ? 'true' : 'false'"
				@click="setLocale(option.code)"
			>
				{{ option.label }}
			</button>
		</div>
	</div>
</template>
