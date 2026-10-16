<script setup lang="ts">
/**
 * Sign-in helper on a demonstration site: the published (fictitious) students and parents,
 * one tap each, and the demonstration code that works for all of them.
 */
import { ArrowRight, FlaskConical } from "@lucide/vue";
import { t } from "@/i18n";
import type { DemoAccount } from "@/utils/boot";
import { DEMO_INFO_URL, demoAccountLabel } from "@/utils/demo";
import { formatMobile } from "@/utils/format";

defineProps<{ accounts: DemoAccount[]; code: string; busy?: boolean }>();
const emit = defineEmits<{ choose: [mobile: string] }>();
</script>

<template>
	<section class="card mt-4 border-warning" aria-labelledby="demo-sign-in-title" data-test="demo-sign-in">
		<h2 id="demo-sign-in-title" class="flex items-center gap-2 font-semibold text-ink">
			<FlaskConical :size="18" class="shrink-0 text-warning" aria-hidden="true" />{{ t("demo.signInTitle") }}
		</h2>
		<p class="mt-1 text-sm text-muted">{{ t("demo.signInBody") }}</p>
		<p v-if="code" class="mt-3 text-sm text-ink">
			{{ t("demo.codeLabel") }}
			<strong
				class="ml-1 rounded-lg bg-surface-2 px-2 py-0.5 font-mono text-base tracking-widest"
				data-test="demo-code"
				>{{ code }}</strong
			>
		</p>
		<ul class="mt-3 grid gap-2">
			<li v-for="account in accounts" :key="account.mobile">
				<button
					type="button"
					class="btn-secondary btn-block min-h-14 justify-start gap-3 text-left"
					:disabled="busy"
					:data-test="`demo-account-${account.mobile}`"
					@click="emit('choose', account.mobile)"
				>
					<span class="min-w-0 flex-1">
						<span class="block truncate font-semibold">{{ account.name }}</span>
						<span class="block text-xs font-normal text-muted">{{ demoAccountLabel(account) }}</span>
						<span class="block font-mono text-xs font-normal text-muted">{{ formatMobile(account.mobile) }}</span>
					</span>
					<ArrowRight :size="18" class="shrink-0" aria-hidden="true" />
				</button>
			</li>
		</ul>
		<p class="mt-3 text-sm">
			<a :href="DEMO_INFO_URL">{{ t("demo.staffHint") }}</a>
		</p>
	</section>
</template>
