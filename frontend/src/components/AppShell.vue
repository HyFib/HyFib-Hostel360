<script setup lang="ts">
/**
 * Signed-in layout: header, bottom navigation on phones and a side rail on wide screens.
 */
import { Bell } from "@lucide/vue";
import { computed, onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import { t } from "@/i18n";
import { refreshUnread, unreadCount } from "@/stores/inbox";
import { useSession } from "@/stores/session";
import DemoBanner from "./DemoBanner.vue";
import InstitutionMark from "./InstitutionMark.vue";
import OfflineBanner from "./OfflineBanner.vue";
import WardSwitcher from "./WardSwitcher.vue";
import { isActive, itemsFor, primaryItemsFor } from "./navigation";

const route = useRoute();
const { session, institutionName } = useSession();

const primary = computed(() => primaryItemsFor(session.kind));
const sideItems = computed(() => itemsFor(session.kind));
const routeName = computed(() => (typeof route.name === "string" ? route.name : null));
// Until the privacy notice is accepted and a student's details are confirmed, the rest of the
// portal is closed, so navigation would only bounce back; it is hidden to keep the first run calm.
const firstRun = computed(() => session.consentRequired || (session.kind === "student" && !session.profileConfirmed));
const moreActive = computed(
	() =>
		routeName.value === "more" ||
		!primary.value.some((item) => item.name !== "more" && isActive(item, routeName.value)),
);

onMounted(() => {
	if (!firstRun.value) void refreshUnread(true);
});
watch(routeName, () => {
	if (!firstRun.value) void refreshUnread();
});
</script>

<template>
	<div class="min-h-dvh" :class="firstRun ? '' : 'lg:pl-64'">
		<a
			href="#main"
			class="sr-only z-50 rounded-lg bg-brand px-4 py-2 font-semibold text-on-brand focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
			>{{ t("common.skipToContent") }}</a
		>

		<header
			class="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur supports-[backdrop-filter]:bg-surface/85"
		>
			<DemoBanner />
			<div class="mx-auto flex h-16 max-w-3xl items-center gap-3 px-4">
				<RouterLink
					:to="{ name: 'home' }"
					class="flex min-w-0 items-center gap-2.5 no-underline"
					:class="firstRun ? '' : 'lg:hidden'"
					:aria-label="t('nav.home')"
				>
					<InstitutionMark :logo="session.settings.logo" :name="institutionName" />
					<span class="min-w-0">
						<span class="block truncate text-[0.95rem] font-bold leading-tight text-ink">{{
							institutionName || t("app.name")
						}}</span>
						<span class="block truncate text-xs text-muted">{{ t("app.portal") }}</span>
					</span>
				</RouterLink>
				<div v-if="!firstRun" class="ml-auto flex items-center gap-1">
					<WardSwitcher />
					<RouterLink
						:to="{ name: 'notifications' }"
						class="icon-btn relative"
						:aria-label="unreadCount ? t('notifications.bellUnread', { count: unreadCount }) : t('notifications.title')"
					>
						<Bell :size="22" aria-hidden="true" />
						<span
							v-if="unreadCount"
							class="absolute top-1.5 right-1.5 inline-flex min-w-5 items-center justify-center rounded-full bg-danger px-1 text-[0.7rem] font-bold leading-5 text-white"
							aria-hidden="true"
							>{{ unreadCount > 9 ? "9+" : unreadCount }}</span
						>
					</RouterLink>
				</div>
			</div>
			<OfflineBanner />
		</header>

		<!-- Side rail for tablets and desktops -->
		<nav
			v-if="!firstRun"
			class="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-line bg-surface lg:flex"
			:aria-label="t('nav.main')"
		>
			<RouterLink :to="{ name: 'home' }" class="flex items-center gap-3 px-5 py-5 no-underline">
				<InstitutionMark :logo="session.settings.logo" :name="institutionName" :size="40" />
				<span class="min-w-0">
					<span class="block truncate font-bold text-ink">{{ institutionName || t("app.name") }}</span>
					<span class="block truncate text-xs text-muted">{{ t("app.portal") }}</span>
				</span>
			</RouterLink>
			<ul class="flex-1 space-y-0.5 overflow-y-auto px-3 pb-6">
				<li v-for="item in sideItems" :key="`${item.name}-${item.label}`">
					<RouterLink
						:to="{ name: item.name }"
						class="flex min-h-11 items-center gap-3 rounded-xl px-3 text-[0.95rem] font-medium no-underline"
						:class="isActive(item, routeName) ? 'bg-brand-soft text-brand-ink' : 'text-ink hover:bg-surface-2'"
						:aria-current="isActive(item, routeName) ? 'page' : undefined"
					>
						<component :is="item.icon" :size="20" aria-hidden="true" />
						{{ t(item.label) }}
					</RouterLink>
				</li>
			</ul>
		</nav>

		<main id="main" tabindex="-1" class="mx-auto max-w-3xl px-4 pt-5 lg:pb-12" :class="firstRun ? 'pb-12' : 'pb-28'">
			<slot />
		</main>

		<!-- Bottom bar for phones -->
		<nav
			v-if="!firstRun"
			class="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface pb-safe lg:hidden"
			:aria-label="t('nav.main')"
		>
			<ul class="mx-auto grid max-w-3xl grid-cols-5">
				<li v-for="item in primary" :key="`${item.name}-${item.label}`">
					<RouterLink
						:to="{ name: item.name }"
						class="flex min-h-16 flex-col items-center justify-center gap-0.5 px-1 text-[0.72rem] font-semibold no-underline"
						:class="(item.name === 'more' ? moreActive : isActive(item, routeName)) ? 'text-brand-ink' : 'text-muted'"
						:aria-current="(item.name === 'more' ? moreActive : isActive(item, routeName)) ? 'page' : undefined"
					>
						<span
							class="inline-flex h-7 w-12 items-center justify-center rounded-full transition-colors"
							:class="(item.name === 'more' ? moreActive : isActive(item, routeName)) ? 'bg-brand-soft' : ''"
							aria-hidden="true"
						>
							<component :is="item.icon" :size="22" />
						</span>
						<span class="max-w-full truncate">{{ t(item.label) }}</span>
					</RouterLink>
				</li>
			</ul>
		</nav>
	</div>
</template>
