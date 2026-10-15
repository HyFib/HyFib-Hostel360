<script setup lang="ts">
/**
 * Modal dialog with focus kept inside while open and returned to the trigger afterwards.
 */
import { X } from "@lucide/vue";
import { nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import { t } from "@/i18n";

// Boolean props default to false in Vue, so dismissible must be turned on explicitly here.
const props = withDefaults(defineProps<{ open: boolean; title: string; dismissible?: boolean }>(), {
	dismissible: true,
});
const emit = defineEmits<{ close: [] }>();

const panel = ref<HTMLElement | null>(null);
const titleId = useId();
let previouslyFocused: HTMLElement | null = null;

const FOCUSABLE =
	'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(): HTMLElement[] {
	return panel.value ? Array.from(panel.value.querySelectorAll<HTMLElement>(FOCUSABLE)) : [];
}

function onKeydown(event: KeyboardEvent) {
	if (event.key === "Escape" && props.dismissible) {
		event.preventDefault();
		emit("close");
		return;
	}
	if (event.key !== "Tab") return;
	const items = focusables();
	if (!items.length) return;
	const first = items[0];
	const last = items[items.length - 1];
	if (event.shiftKey && document.activeElement === first) {
		event.preventDefault();
		last.focus();
	} else if (!event.shiftKey && document.activeElement === last) {
		event.preventDefault();
		first.focus();
	}
}

watch(
	() => props.open,
	async (open) => {
		if (open) {
			previouslyFocused = document.activeElement as HTMLElement | null;
			document.body.style.overflow = "hidden";
			await nextTick();
			(panel.value?.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0])?.focus();
		} else {
			document.body.style.overflow = "";
			previouslyFocused?.focus?.();
		}
	},
	{ immediate: true },
);

onBeforeUnmount(() => {
	document.body.style.overflow = "";
});
</script>

<template>
	<Teleport to="body">
		<div
			v-if="open"
			class="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4"
			@click.self="dismissible && emit('close')"
		>
			<div
				ref="panel"
				role="dialog"
				aria-modal="true"
				:aria-labelledby="titleId"
				class="max-h-[90dvh] w-full overflow-y-auto rounded-t-2xl bg-surface p-5 shadow-xl pb-safe sm:max-w-md sm:rounded-2xl"
				@keydown="onKeydown"
			>
				<div class="mb-3 flex items-start justify-between gap-3">
					<h2 :id="titleId" class="text-lg font-semibold text-ink">{{ title }}</h2>
					<button
						v-if="dismissible"
						type="button"
						class="icon-btn -mr-2 -mt-2"
						:aria-label="t('common.close')"
						@click="emit('close')"
					>
						<X :size="20" aria-hidden="true" />
					</button>
				</div>
				<slot />
			</div>
		</div>
	</Teleport>
</template>
