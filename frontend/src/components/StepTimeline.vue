<script setup lang="ts">
/** Vertical progress list: done, current and upcoming steps (leave approval flow). */
import { Check, Circle, X } from "@lucide/vue";
import type { TimelineStep } from "@/types/ui";

defineProps<{ steps: TimelineStep[]; label: string }>();
</script>

<template>
	<ol class="relative space-y-3" :aria-label="label">
		<li v-for="(step, index) in steps" :key="step.key" class="relative flex gap-3">
			<span
				v-if="index < steps.length - 1"
				class="absolute left-[0.6875rem] top-6 h-[calc(100%-0.5rem)] w-0.5"
				:class="step.state === 'done' ? 'bg-success' : 'bg-line'"
				aria-hidden="true"
			/>
			<span
				class="relative z-10 mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full"
				:class="{
					'bg-success text-white': step.state === 'done',
					'bg-brand text-on-brand': step.state === 'current',
					'bg-danger text-white': step.state === 'failed',
					'border-2 border-line bg-surface text-muted': step.state === 'upcoming' || step.state === 'skipped',
				}"
				aria-hidden="true"
			>
				<Check v-if="step.state === 'done'" :size="14" :stroke-width="3" />
				<X v-else-if="step.state === 'failed'" :size="14" :stroke-width="3" />
				<Circle v-else-if="step.state === 'current'" :size="8" class="fill-current" />
			</span>
			<div class="min-w-0 pb-1">
				<p
					class="text-sm font-semibold"
					:class="step.state === 'upcoming' || step.state === 'skipped' ? 'text-muted' : 'text-ink'"
				>
					{{ step.label }}
				</p>
				<p v-if="step.detail" class="text-sm text-muted">{{ step.detail }}</p>
			</div>
		</li>
	</ol>
</template>
