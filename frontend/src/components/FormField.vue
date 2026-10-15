<script setup lang="ts">
import { CircleAlert } from "@lucide/vue";
import { computed, useId } from "vue";
import { t } from "@/i18n";

const props = defineProps<{
	label: string;
	hint?: string;
	error?: string;
	required?: boolean;
	/** Use when the control renders its own label (switches, star ratings). */
	as?: "div";
}>();

const id = useId();
const hintId = `${id}-hint`;
const errorId = `${id}-error`;
const describedBy = computed(
	() => [props.error ? errorId : "", props.hint ? hintId : ""].filter(Boolean).join(" ") || undefined,
);
const invalid = computed<"true" | undefined>(() => (props.error ? "true" : undefined));
</script>

<template>
	<div class="mb-4">
		<label v-if="as !== 'div'" :for="id" class="field-label">
			{{ label }}
			<span v-if="!required" class="font-normal text-muted">{{ t("common.optional") }}</span>
		</label>
		<slot :id="id" :described-by="describedBy" :invalid="invalid" :label="label" />
		<p v-if="error" :id="errorId" class="field-error">
			<CircleAlert :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />
			<span>{{ error }}</span>
		</p>
		<p v-if="hint" :id="hintId" class="field-hint">{{ hint }}</p>
	</div>
</template>
