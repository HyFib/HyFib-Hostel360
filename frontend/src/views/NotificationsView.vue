<script setup lang="ts">
import { BellOff, CheckCheck } from "@lucide/vue";
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { errorMessage } from "@/api/client";
import { listNotifications, markNotificationsRead } from "@/api/portal";
import type { PortalNotification } from "@/api/types";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { useResource } from "@/composables/useResource";
import { t } from "@/i18n";
import { setUnread } from "@/stores/inbox";
import { showToast } from "@/stores/toast";
import { formatDateTime } from "@/utils/format";

const router = useRouter();
const notifications = useResource(() => listNotifications(50));
const list = computed(() => notifications.data.value ?? []);
const unread = computed(() => list.value.filter((item) => !item.read));
const marking = ref(false);

function portalPath(link?: string | null): string | null {
	if (!link) return null;
	if (link === "/hostel") return "/";
	return link.startsWith("/hostel/") ? link.slice("/hostel".length) : null;
}

function setRead(names: string[]) {
	notifications.data.value = list.value.map((item) => (names.includes(item.name) ? { ...item, read: 1 } : item));
	setUnread(unread.value.length);
}

async function markAll() {
	if (!unread.value.length) return;
	marking.value = true;
	const before = list.value;
	setRead(unread.value.map((item) => item.name));
	try {
		await markNotificationsRead();
		showToast(t("notifications.markedRead"));
	} catch (error) {
		notifications.data.value = before;
		setUnread(before.filter((item) => !item.read).length);
		showToast(errorMessage(error), { tone: "error" });
	} finally {
		marking.value = false;
	}
}

async function openItem(item: PortalNotification) {
	if (!item.read) {
		setRead([item.name]);
		void markNotificationsRead([item.name]).catch(() => undefined);
	}
	const path = portalPath(item.link);
	if (path) await router.push(path);
}
</script>

<template>
	<div>
		<PageHeader :title="t('notifications.title')">
			<template #actions>
				<button v-if="unread.length" type="button" class="btn-ghost min-h-10 px-3 text-sm" :disabled="marking" @click="markAll">
					<CheckCheck :size="18" aria-hidden="true" />{{ t("notifications.markAllRead") }}
				</button>
			</template>
		</PageHeader>

		<SkeletonList v-if="notifications.loading.value && !notifications.data.value" :rows="4" />
		<ErrorState v-else-if="notifications.error.value" :error="notifications.error.value" @retry="notifications.reload" />
		<EmptyState v-else-if="!list.length" :icon="BellOff" :title="t('notifications.empty')" :body="t('notifications.emptyHint')" />

		<ul v-else class="card list-divided p-0">
			<li v-for="item in list" :key="item.name">
				<button type="button" class="flex w-full items-start gap-3 p-4 text-left hover:bg-surface-2" @click="openItem(item)">
					<span class="mt-1.5 size-2.5 shrink-0 rounded-full" :class="item.read ? 'bg-transparent' : 'bg-brand'" aria-hidden="true" />
					<span class="min-w-0 flex-1">
						<span class="block text-[0.95rem]" :class="item.read ? 'text-ink' : 'font-semibold text-ink'">{{ item.subject }}</span>
						<span class="mt-0.5 block text-xs text-muted">
							{{ formatDateTime(item.created) }}<template v-if="!item.read"> · {{ t("notifications.unread") }}</template>
						</span>
					</span>
				</button>
			</li>
		</ul>
	</div>
</template>
