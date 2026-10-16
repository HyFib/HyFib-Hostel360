<script setup lang="ts">
import { useId } from "vue";

defineProps<{ label: string; hint?: string; disabled?: boolean }>();
const model = defineModel<boolean>({ required: true });
const id = useId();
</script>

<template>
	<div class="mb-4 flex items-start justify-between gap-4">
		<div class="min-w-0">
			<span :id="`${id}-label`" class="block text-sm font-medium text-ink">{{ label }}</span>
			<span v-if="hint" :id="`${id}-hint`" class="mt-0.5 block text-sm text-muted">{{ hint }}</span>
		</div>
		<button
			type="button"
			role="switch"
			class="switch mt-0.5"
			:aria-checked="model ? 'true' : 'false'"
			:aria-labelledby="`${id}-label`"
			:aria-describedby="hint ? `${id}-hint` : undefined"
			:disabled="disabled"
			@click="model = !model"
		>
			<span class="switch-thumb" aria-hidden="true" />
		</button>
	</div>
</template>
