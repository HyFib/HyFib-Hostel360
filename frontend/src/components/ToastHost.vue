<script setup lang="ts">
import { CircleAlert, CircleCheck, Info, X } from "@lucide/vue";
import { computed } from "vue";
import { t } from "@/i18n";
import { dismissToast, toasts, type Toast } from "@/stores/toast";

const polite = computed(() => toasts.filter((item) => item.tone !== "error"));
const urgent = computed(() => toasts.filter((item) => item.tone === "error"));

function act(item: Toast) {
	item.action?.();
	dismissToast(item.id);
}
</script>

<template>
	<div
		class="pointer-events-none fixed inset-x-0 bottom-20 z-40 flex flex-col items-center gap-2 px-4 lg:bottom-6 lg:left-64"
	>
		<div aria-live="assertive" class="flex w-full flex-col items-center gap-2">
			<div
				v-for="item in urgent"
				:key="item.id"
				role="alert"
				class="pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger shadow-lg ring-1 ring-danger"
			>
				<CircleAlert :size="20" class="mt-0.5 shrink-0" aria-hidden="true" />
				<p class="min-w-0 flex-1 whitespace-pre-line">{{ item.message }}</p>
				<button
					type="button"
					class="-m-2 inline-flex size-10 items-center justify-center rounded-full"
					:aria-label="t('common.dismiss')"
					@click="dismissToast(item.id)"
				>
					<X :size="18" aria-hidden="true" />
				</button>
			</div>
		</div>
		<div aria-live="polite" class="flex w-full flex-col items-center gap-2">
			<div
				v-for="item in polite"
				:key="item.id"
				class="pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-xl bg-ink p-3 text-sm font-medium text-bg shadow-lg"
			>
				<CircleCheck v-if="item.tone === 'success'" :size="20" class="shrink-0" aria-hidden="true" />
				<Info v-else :size="20" class="shrink-0" aria-hidden="true" />
				<p class="min-w-0 flex-1">{{ item.message }}</p>
				<button
					v-if="item.actionLabel"
					type="button"
					class="min-h-10 shrink-0 rounded-lg px-3 font-semibold underline underline-offset-2"
					@click="act(item)"
				>
					{{ item.actionLabel }}
				</button>
				<button
					v-else
					type="button"
					class="-m-2 inline-flex size-10 shrink-0 items-center justify-center rounded-full"
					:aria-label="t('common.dismiss')"
					@click="dismissToast(item.id)"
				>
					<X :size="18" aria-hidden="true" />
				</button>
			</div>
		</div>
	</div>
</template>
