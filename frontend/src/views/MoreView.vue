<script setup lang="ts">
import { ChevronRight } from "@lucide/vue";
import { computed } from "vue";
import { itemsFor } from "@/components/navigation";
import LanguageSwitch from "@/components/LanguageSwitch.vue";
import PageHeader from "@/components/PageHeader.vue";
import { t } from "@/i18n";
import { useSession } from "@/stores/session";

const { session } = useSession();
// Everything that is not already in the bottom bar.
const items = computed(() => itemsFor(session.kind).filter((item) => !item.primary));
</script>

<template>
	<div>
		<PageHeader :title="t('nav.more')" />
		<ul class="card list-divided p-0">
			<li v-for="item in items" :key="`${item.name}-${item.label}`">
				<RouterLink
					:to="{ name: item.name }"
					class="flex min-h-14 items-center gap-3 px-4 text-ink no-underline hover:bg-surface-2"
				>
					<span
						class="inline-flex size-9 items-center justify-center rounded-full bg-brand-soft text-brand-ink"
						aria-hidden="true"
					>
						<component :is="item.icon" :size="18" />
					</span>
					<span class="flex-1 font-medium">{{ t(item.label) }}</span>
					<ChevronRight :size="18" class="text-muted" aria-hidden="true" />
				</RouterLink>
			</li>
		</ul>
		<div class="mt-6">
			<p class="field-label">{{ t("profile.language") }}</p>
			<LanguageSwitch />
		</div>
	</div>
</template>
