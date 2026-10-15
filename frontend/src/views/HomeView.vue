<script setup lang="ts">
import {
	Award,
	BedDouble,
	Bell,
	CalendarDays,
	CalendarOff,
	ChevronRight,
	CirclePause,
	ClipboardCheck,
	Clock,
	MessageSquareWarning,
	Utensils,
	Wallet,
} from "@lucide/vue";
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { getDashboard } from "@/api/portal";
import type { PortalNotification } from "@/api/types";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { useResource } from "@/composables/useResource";
import { t, tv } from "@/i18n";
import { useSession } from "@/stores/session";
import { formatDate, formatDateTime, formatINR, greetingKey, toNumber } from "@/utils/format";

const route = useRoute();
const router = useRouter();
const {
	session,
	activeWard,
	isParent,
	isValidated,
	hasFullAccess,
	effectiveValidationStatus,
	studentStatus,
	firstName,
	paymentEnabled,
	setActiveStudent,
} = useSession();

// A pending ward's dashboard is refused by the server; there is nothing to load for it.
const dashboard = useResource(
	() => (isValidated.value ? getDashboard(isParent.value ? session.activeStudent : undefined) : Promise.resolve(null)),
	{ watch: [() => session.activeStudent] },
);

const greeting = computed(() => t(`home.greeting.${greetingKey()}`, { name: firstName.value }));
const subtitle = computed(() =>
	isParent.value && activeWard.value
		? t("home.subtitleParent", { name: activeWard.value.full_name })
		: t("home.subtitleStudent", { date: formatDate(new Date()) }),
);
const showVerification = computed(() => !isValidated.value || route.query.notice === "verification");
const verificationText = computed(() => {
	const rejected = effectiveValidationStatus.value === "Rejected";
	if (isParent.value && activeWard.value) {
		const name = activeWard.value.full_name;
		return rejected ? t("home.wardVerificationRejected", { name }) : t("home.wardVerificationPending", { name });
	}
	return rejected ? t("home.verificationRejected") : t("home.verificationPending");
});

const data = computed(() => dashboard.data.value);
/** Validated but not Active (Applied, Suspended…): only dues and the basics are open. */
const restricted = computed(() => isValidated.value && (Boolean(data.value?.restricted) || !hasFullAccess.value));
const restrictedText = computed(() => {
	const status = tv("status", data.value?.student_status || studentStatus.value || "");
	return isParent.value && activeWard.value
		? t("home.wardRestrictedBody", { name: activeWard.value.full_name, status })
		: t("home.restrictedBody", { status });
});
const unavailable = computed(() => new Set(data.value?.unavailable ?? []));
/** The fee figures could not be loaded: never show that as "no dues". */
const feesUnavailable = computed(() => Boolean(data.value) && (!data.value?.fees || unavailable.value.has("fees")));
const room = computed(() => data.value?.room ?? null);
const outstanding = computed(() => toNumber(data.value?.fees?.outstanding));
const overdue = computed(() => toNumber(data.value?.fees?.overdue));
const menu = computed(() => data.value?.today_menu ?? null);
const notifications = computed(() => (data.value?.notifications ?? []).slice(0, 5));

const pendingRows = computed(() => {
	const pending = data.value?.pending ?? {};
	const rows = [
		{ key: "leaves", label: t("home.pendingLeaves"), count: pending.leaves ?? 0, to: { name: "leave" }, icon: CalendarDays },
		{ key: "reductions", label: t("home.pendingReductions"), count: pending.reductions ?? 0, to: { name: "mess-reduction" }, icon: CalendarOff },
		{ key: "grievances", label: t("home.pendingGrievances"), count: pending.grievances ?? 0, to: { name: "grievances" }, icon: MessageSquareWarning },
		{ key: "certificates", label: t("home.pendingCertificates"), count: pending.certificates ?? 0, to: { name: "certificates" }, icon: Award },
	];
	if (isParent.value) {
		rows.unshift({
			key: "approvals",
			label: t("home.pendingApprovals"),
			count: pending.approvals ?? 0,
			to: { name: "leave-approvals" },
			icon: ClipboardCheck,
		});
	}
	return rows;
});
const anyPending = computed(() => pendingRows.value.some((row) => row.count > 0));

const quickActions = computed(() => {
	const actions = [
		{ label: t("home.applyLeave"), to: { name: "leave-new" }, icon: CalendarDays },
		{ label: t("home.raiseGrievance"), to: { name: "grievance-new" }, icon: MessageSquareWarning },
		{ label: t("home.messReduction"), to: { name: "mess-reduction-new" }, icon: CalendarOff },
		{ label: t("home.certificate"), to: { name: "certificates" }, icon: Award },
	];
	if (isParent.value) actions.unshift({ label: t("home.approvals"), to: { name: "leave-approvals" }, icon: ClipboardCheck });
	return actions.slice(0, 4);
});

function roomLine(): string {
	const value = room.value;
	if (!value) return "";
	return [value.block_name || value.block, value.floor_label].filter(Boolean).join(" · ");
}

function internalLink(link?: string | null): string | null {
	if (!link) return null;
	if (link.startsWith("/hostel/")) return link.slice("/hostel".length);
	return null;
}

function notificationHref(item: PortalNotification): string | undefined {
	const path = internalLink(item.link);
	return path ? router.resolve(path).href : undefined;
}

/** Open a notification, first switching to the ward it is about (a parent may have several). */
function onNotificationClick(event: MouseEvent, item: PortalNotification) {
	const path = internalLink(item.link);
	if (!path) return;
	event.preventDefault();
	if (isParent.value && item.student && item.student !== session.activeStudent) setActiveStudent(item.student);
	void router.push(path);
}
</script>

<template>
	<div>
		<header class="mb-5">
			<h1 class="text-2xl font-bold tracking-tight text-ink" tabindex="-1" data-page-title>{{ greeting }}</h1>
			<p class="mt-1 text-[0.95rem] text-muted">{{ subtitle }}</p>
		</header>

		<div v-if="showVerification && !isValidated" class="card mb-4 flex items-start gap-3 border-warning bg-warning-soft" role="status">
			<Clock :size="22" class="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
			<div>
				<p class="font-semibold text-ink">{{ t("home.verificationTitle") }}</p>
				<p class="mt-0.5 text-sm text-muted">{{ verificationText }}</p>
				<RouterLink :to="{ name: 'help' }" class="mt-2 inline-flex min-h-11 items-center text-sm font-semibold">{{ t("home.contactOffice") }}</RouterLink>
			</div>
		</div>

		<div v-if="restricted" class="card mb-4 flex items-start gap-3 border-warning bg-warning-soft" role="status" data-test="restricted-notice">
			<CirclePause :size="22" class="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
			<div>
				<p class="font-semibold text-ink">{{ t("home.restrictedTitle") }}</p>
				<p class="mt-0.5 text-sm text-muted">{{ restrictedText }}</p>
				<RouterLink :to="{ name: 'help' }" class="mt-2 inline-flex min-h-11 items-center text-sm font-semibold">{{ t("home.contactOffice") }}</RouterLink>
			</div>
		</div>

		<SkeletonList v-if="dashboard.loading.value && !data" :rows="3" card />
		<ErrorState v-else-if="dashboard.error.value && isValidated" :error="dashboard.error.value" @retry="dashboard.reload" />

		<div v-else-if="data" class="grid gap-4 sm:grid-cols-2">
			<!-- Dues -->
			<section class="card sm:col-span-2" aria-labelledby="home-dues">
				<div class="flex items-start justify-between gap-3">
					<div>
						<h2 id="home-dues" class="flex items-center gap-2 text-sm font-semibold text-muted">
							<Wallet :size="18" aria-hidden="true" />{{ t("home.duesTitle") }}
						</h2>
						<p v-if="feesUnavailable" class="mt-1 text-sm text-muted" data-test="dues-unavailable">{{ t("home.duesUnavailable") }}</p>
						<template v-else>
							<p class="mt-1 text-3xl font-bold tracking-tight text-ink">{{ formatINR(outstanding) }}</p>
							<p v-if="outstanding <= 0" class="mt-1 text-sm text-success">{{ t("home.noDues") }}</p>
							<p v-else class="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
								<span v-if="overdue > 0" class="chip tone-danger">{{ t("home.overdue", { amount: formatINR(overdue) }) }}</span>
								<span v-if="data.fees?.next_due_date">{{ t("home.nextDue", { date: formatDate(data.fees.next_due_date) }) }}</span>
							</p>
						</template>
					</div>
				</div>
				<div class="mt-4 flex flex-wrap gap-2">
					<RouterLink v-if="!feesUnavailable && outstanding > 0 && paymentEnabled" :to="{ name: 'pay' }" class="btn-primary">
						{{ t("home.pay") }}
					</RouterLink>
					<RouterLink
						:to="{ name: 'fees' }"
						:class="!feesUnavailable && outstanding > 0 && paymentEnabled ? 'btn-secondary' : 'btn-primary'"
					>
						{{ t("home.viewFees") }}
					</RouterLink>
				</div>
			</section>

			<!-- Room -->
			<section class="card" aria-labelledby="home-room">
				<h2 id="home-room" class="flex items-center gap-2 text-sm font-semibold text-muted">
					<BedDouble :size="18" aria-hidden="true" />{{ isParent ? t("home.wardRoomTitle") : t("home.roomTitle") }}
				</h2>
				<template v-if="room && (room.room || room.room_number)">
					<p class="mt-2 text-lg font-bold text-ink">{{ room.hostel_name || room.hostel }}</p>
					<p v-if="roomLine()" class="text-sm text-muted">{{ roomLine() }}</p>
					<dl class="mt-3 grid grid-cols-2 gap-3">
						<div class="rounded-xl bg-surface-2 p-3">
							<dt class="text-xs font-medium text-muted">{{ t("fields.room") }}</dt>
							<dd class="text-lg font-bold text-ink">{{ room.room_number || room.room }}</dd>
						</div>
						<div class="rounded-xl bg-surface-2 p-3">
							<dt class="text-xs font-medium text-muted">{{ t("fields.bed") }}</dt>
							<dd class="text-lg font-bold text-ink">{{ room.bed_label || room.bed || "—" }}</dd>
						</div>
					</dl>
				</template>
				<p v-else-if="unavailable.has('profile')" class="mt-2 text-sm text-muted">{{ t("home.roomUnavailable") }}</p>
				<p v-else class="mt-2 text-sm text-muted">{{ t("home.noRoom") }}</p>
			</section>

			<!-- Today's menu -->
			<section v-if="!restricted" class="card" aria-labelledby="home-menu">
				<div class="flex items-center justify-between gap-2">
					<h2 id="home-menu" class="flex items-center gap-2 text-sm font-semibold text-muted">
						<Utensils :size="18" aria-hidden="true" />{{ t("home.menuTitle") }}
					</h2>
					<RouterLink :to="{ name: 'menu' }" class="inline-flex min-h-11 items-center text-sm font-semibold">{{ t("home.fullMenu") }}</RouterLink>
				</div>
				<ul v-if="menu && menu.meals.length" class="mt-1 space-y-2">
					<li v-for="meal in menu.meals" :key="meal.meal" class="flex gap-3">
						<span class="w-20 shrink-0 text-sm font-semibold text-brand-ink">{{ tv("meals", meal.meal) }}</span>
						<span class="min-w-0 text-sm text-ink">
							{{ meal.dishes }}
							<span v-if="meal.is_special" class="chip tone-info ml-1">{{ t("menu.special") }}</span>
						</span>
					</li>
				</ul>
				<p v-else-if="unavailable.has('menu')" class="mt-2 text-sm text-muted">{{ t("home.menuUnavailable") }}</p>
				<p v-else class="mt-2 text-sm text-muted">{{ t("home.noMenu") }}</p>
			</section>

			<!-- Pending -->
			<section v-if="!restricted" class="card" aria-labelledby="home-pending">
				<h2 id="home-pending" class="text-sm font-semibold text-muted">{{ t("home.pendingTitle") }}</h2>
				<ul class="list-divided -mx-1 mt-1">
					<li v-for="row in pendingRows" :key="row.key">
						<RouterLink :to="row.to" class="flex min-h-12 items-center gap-3 rounded-lg px-1 text-ink no-underline">
							<component :is="row.icon" :size="18" class="shrink-0 text-muted" aria-hidden="true" />
							<span class="flex-1 text-sm">{{ row.label }}</span>
							<span class="chip" :class="row.count > 0 ? 'tone-warning' : 'tone-neutral'">{{ row.count }}</span>
							<ChevronRight :size="16" class="text-muted" aria-hidden="true" />
						</RouterLink>
					</li>
				</ul>
				<p v-if="unavailable.has('pending')" class="mt-1 text-xs text-muted">{{ t("home.pendingUnavailable") }}</p>
				<p v-else-if="!anyPending" class="mt-1 text-xs text-muted">{{ t("home.nothingPending") }}</p>
			</section>

			<!-- Quick actions -->
			<section v-if="!restricted" class="card" aria-labelledby="home-actions">
				<h2 id="home-actions" class="text-sm font-semibold text-muted">{{ t("home.quickTitle") }}</h2>
				<div class="mt-3 grid grid-cols-2 gap-2">
					<RouterLink
						v-for="action in quickActions"
						:key="action.label"
						:to="action.to"
						class="flex min-h-20 flex-col items-start justify-between gap-2 rounded-xl bg-brand-soft p-3 text-sm font-semibold text-brand-ink no-underline"
					>
						<component :is="action.icon" :size="20" aria-hidden="true" />
						<span>{{ action.label }}</span>
					</RouterLink>
				</div>
			</section>

			<!-- Notifications -->
			<section class="card sm:col-span-2" aria-labelledby="home-updates">
				<div class="flex items-center justify-between gap-2">
					<h2 id="home-updates" class="flex items-center gap-2 text-sm font-semibold text-muted">
						<Bell :size="18" aria-hidden="true" />{{ t("home.notificationsTitle") }}
					</h2>
					<RouterLink :to="{ name: 'notifications' }" class="inline-flex min-h-11 items-center text-sm font-semibold">{{ t("common.seeAll") }}</RouterLink>
				</div>
				<ul v-if="notifications.length" class="list-divided">
					<li v-for="item in notifications" :key="item.name" class="py-2.5">
						<component
							:is="internalLink(item.link) ? 'a' : 'div'"
							:href="notificationHref(item)"
							class="flex items-start gap-2 text-ink no-underline"
							@click="onNotificationClick($event, item)"
						>
							<span class="mt-2 size-2 shrink-0 rounded-full" :class="item.read ? 'bg-transparent' : 'bg-brand'" aria-hidden="true" />
							<span class="min-w-0">
								<span v-if="!item.read" class="sr-only">{{ t("notifications.unread") }}: </span>
								<span class="block text-sm" :class="item.read ? '' : 'font-semibold'">{{ item.subject }}</span>
								<span v-if="item.message" class="block text-sm text-muted line-clamp-2">{{ item.message }}</span>
								<span class="block text-xs text-muted">{{ formatDateTime(item.created) }}</span>
							</span>
						</component>
					</li>
				</ul>
				<p v-else class="mt-2 text-sm text-muted">{{ t("home.noNotifications") }}</p>
			</section>
		</div>

		<EmptyState v-else-if="!isValidated" :title="t('home.limitedTitle')" :body="t('home.limitedBody')">
			<RouterLink :to="{ name: 'profile' }" class="btn-secondary">{{ t("nav.profile") }}</RouterLink>
			<RouterLink :to="{ name: 'help' }" class="btn-secondary">{{ t("nav.help") }}</RouterLink>
		</EmptyState>
	</div>
</template>
