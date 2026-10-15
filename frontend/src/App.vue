<script setup lang="ts">
import { WifiOff } from "@lucide/vue";
import { computed, nextTick, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppShell from "@/components/AppShell.vue";
import ConfirmHost from "@/components/ConfirmHost.vue";
import OfflineBanner from "@/components/OfflineBanner.vue";
import ToastHost from "@/components/ToastHost.vue";
import { locale, t } from "@/i18n";
import { loadSession, session } from "@/stores/session";

const route = useRoute();
const router = useRouter();
const announcement = ref("");
const retrying = ref(false);
const ready = ref(false);
void router.isReady().then(() => {
	ready.value = true;
});

const bare = computed(() => !session.loggedIn || route.meta.bare === true);
const unreachable = computed(() => session.status === "unreachable");

// Move focus to the new page's heading so screen-reader and keyboard users start there.
let firstNavigation = true;
router.afterEach((to, from) => {
	if (firstNavigation) {
		firstNavigation = false;
		return;
	}
	if (to.path === from.path) return;
	void nextTick(() => {
		const heading = document.querySelector<HTMLElement>("[data-page-title]");
		heading?.focus({ preventScroll: true });
		announcement.value = heading?.textContent?.trim() || document.title;
	});
});

// Keep the browser tab title in the chosen language.
watch(locale, () => {
	const page = route.meta.title ? t(route.meta.title) : "";
	document.title = [page, session.settings.institution_short_name || "Hostel360"].filter(Boolean).join(" · ");
});

async function retry() {
	retrying.value = true;
	try {
		await loadSession(true);
		if (session.status !== "unreachable") await router.replace(route.fullPath);
	} finally {
		retrying.value = false;
	}
}
</script>

<template>
	<div v-if="unreachable" class="flex min-h-dvh flex-col">
		<OfflineBanner />
		<main class="mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center px-6 text-center">
			<span class="mb-4 inline-flex size-16 items-center justify-center rounded-full bg-warning-soft text-warning" aria-hidden="true">
				<WifiOff :size="30" />
			</span>
			<h1 class="text-xl font-bold text-ink" tabindex="-1" data-page-title>{{ t("network.offlineTitle") }}</h1>
			<p class="mt-2 text-muted">{{ t("network.offlineBody") }}</p>
			<button type="button" class="btn-primary mt-6" :disabled="retrying" @click="retry">
				{{ retrying ? t("common.loading") : t("common.retry") }}
			</button>
		</main>
	</div>
	<div v-else-if="!ready" class="boot-splash" role="status">
		<span class="boot-splash__mark" aria-hidden="true" />
		<span class="boot-splash__text">{{ t("common.loading") }}</span>
	</div>
	<template v-else>
		<RouterView v-if="bare" v-slot="{ Component }">
			<OfflineBanner />
			<component :is="Component" />
		</RouterView>
		<AppShell v-else>
			<RouterView />
		</AppShell>
	</template>
	<ToastHost />
	<ConfirmHost />
	<p class="sr-only" aria-live="polite" aria-atomic="true">{{ announcement }}</p>
</template>
