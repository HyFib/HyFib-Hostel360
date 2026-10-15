<script setup lang="ts">
import { Star } from "@lucide/vue";
import { useId } from "vue";
import { t } from "@/i18n";

const props = withDefaults(defineProps<{ label: string; max?: number; error?: string; disabled?: boolean }>(), {
	max: 5,
});
const model = defineModel<number>({ default: 0 });
const name = useId();
const stars = Array.from({ length: props.max }, (_, index) => index + 1);
</script>

<template>
	<fieldset class="mb-4" :aria-describedby="error ? `${name}-error` : undefined">
		<legend class="field-label">{{ label }}</legend>
		<div class="flex items-center gap-1">
			<label
				v-for="value in stars"
				:key="value"
				class="relative inline-flex size-11 cursor-pointer items-center justify-center rounded-full has-[:focus-visible]:outline-3 has-[:focus-visible]:outline-focus"
			>
				<input v-model="model" class="sr-only" type="radio" :name="name" :value="value" :disabled="disabled" />
				<Star
					:size="30"
					:stroke-width="1.75"
					aria-hidden="true"
					:class="value <= model ? 'fill-amber-400 text-amber-500' : 'text-muted'"
				/>
				<span class="sr-only">{{ t("common.stars", { count: value, max }) }}</span>
			</label>
			<span class="ml-2 text-sm text-muted" aria-hidden="true">{{ model ? `${model}/${max}` : "" }}</span>
		</div>
		<p v-if="error" :id="`${name}-error`" class="field-error">{{ error }}</p>
	</fieldset>
</template>
