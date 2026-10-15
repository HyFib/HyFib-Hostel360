<script setup lang="ts">
/**
 * One box per digit for the sign-in code.
 *
 * The first box carries autocomplete="one-time-code" so Android and iOS can offer the code
 * from the SMS; whatever arrives there (a paste, an autofill, a fast typist) is spread across
 * the boxes. Only digits are accepted.
 */
import { nextTick, ref, watch } from "vue";
import { t } from "@/i18n";

const props = withDefaults(
	defineProps<{
		length?: number;
		disabled?: boolean;
		invalid?: boolean;
		describedBy?: string;
		labelledBy?: string;
		autofocus?: boolean;
	}>(),
	{ length: 6 },
);

const model = defineModel<string>({ default: "" });
const emit = defineEmits<{ complete: [code: string] }>();

const boxes = ref<HTMLInputElement[]>([]);
const digits = ref<string[]>(Array.from({ length: props.length }, (_, i) => model.value[i] ?? ""));

watch(model, (value) => {
	const next = Array.from({ length: props.length }, (_, i) => value[i] ?? "");
	if (next.join("") !== digits.value.join("")) digits.value = next;
});

function focusBox(index: number) {
	const box = boxes.value[Math.max(0, Math.min(index, props.length - 1))];
	if (box) {
		box.focus();
		box.select();
	}
}

function commit() {
	const code = digits.value.join("");
	model.value = code;
	if (code.length === props.length && /^\d+$/.test(code)) emit("complete", code);
}

/** Spread a run of digits from `start`, as from a paste or SMS autofill. */
function fill(start: number, text: string) {
	const incoming = text.replace(/\D/g, "").slice(0, props.length);
	if (!incoming) return;
	// A full code always starts at the first box, wherever the cursor was.
	const from = incoming.length >= props.length ? 0 : start;
	const next = [...digits.value];
	for (let i = 0; i < incoming.length && from + i < props.length; i++) {
		next[from + i] = incoming[i];
	}
	digits.value = next;
	commit();
	focusBox(Math.min(from + incoming.length, props.length - 1));
}

function onInput(index: number, event: Event) {
	const input = event.target as HTMLInputElement;
	const value = input.value.replace(/\D/g, "");
	if (value.length > 1) {
		fill(index, value);
		return;
	}
	const next = [...digits.value];
	next[index] = value;
	digits.value = next;
	// Keep the box showing exactly the stored digit (drops letters typed on a full keyboard).
	input.value = value;
	commit();
	if (value && index < props.length - 1) focusBox(index + 1);
}

function onKeydown(index: number, event: KeyboardEvent) {
	if (event.key === "Backspace" && !digits.value[index] && index > 0) {
		event.preventDefault();
		const next = [...digits.value];
		next[index - 1] = "";
		digits.value = next;
		commit();
		focusBox(index - 1);
	} else if (event.key === "ArrowLeft" && index > 0) {
		event.preventDefault();
		focusBox(index - 1);
	} else if (event.key === "ArrowRight" && index < props.length - 1) {
		event.preventDefault();
		focusBox(index + 1);
	}
}

function onPaste(index: number, event: ClipboardEvent) {
	const text = event.clipboardData?.getData("text") ?? "";
	if (!text) return;
	event.preventDefault();
	fill(index, text);
}

function clear() {
	digits.value = Array.from({ length: props.length }, () => "");
	model.value = "";
	void nextTick(() => focusBox(0));
}

defineExpose({ focus: () => focusBox(digits.value.findIndex((d) => !d)), clear });
</script>

<template>
	<div class="flex justify-between gap-1.5 xs:gap-2" role="group" :aria-labelledby="labelledBy">
		<input
			v-for="(digit, index) in digits"
			:key="index"
			:ref="(el) => { if (el) boxes[index] = el as HTMLInputElement }"
			:value="digit"
			class="input h-14 w-full min-w-0 max-w-14 px-0 text-center text-2xl font-semibold tracking-normal"
			type="text"
			inputmode="numeric"
			pattern="[0-9]*"
			:autocomplete="index === 0 ? 'one-time-code' : 'off'"
			:autofocus="autofocus && index === 0"
			:aria-label="t('login.digitLabel', { n: index + 1, total: length })"
			:aria-invalid="invalid ? 'true' : undefined"
			:aria-describedby="describedBy"
			:disabled="disabled"
			:data-index="index"
			@input="onInput(index, $event)"
			@keydown="onKeydown(index, $event)"
			@paste="onPaste(index, $event)"
			@focus="($event.target as HTMLInputElement).select()"
		>
	</div>
</template>
