<script setup lang="ts">
/** Weekly mess menu with a tab per day; today is selected and marked. */
import { CalendarOff, ChevronLeft, ChevronRight, Utensils, UtensilsCrossed } from "@lucide/vue";
import { computed, nextTick, ref, watch } from "vue";
import { getMenu } from "@/api/portal";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { useResource } from "@/composables/useResource";
import { t, tv } from "@/i18n";
import { studentArg, useSession } from "@/stores/session";
import { formatDate, formatDateRange, parseDate, siteNow, toISODate } from "@/utils/format";

const { session, siteTimeZone } = useSession();
const weekStart = ref<string | null>(null);
const menu = useResource(() => getMenu(weekStart.value, studentArg()), {
	watch: [weekStart, () => session.activeStudent],
});

const days = computed(() => menu.data.value?.days ?? []);
const selectedIndex = ref(0);
const tabs = ref<HTMLButtonElement[]>([]);
// The server marks today (is_today); this is the fallback, on the hostel's clock.
const todayISO = toISODate(siteNow(siteTimeZone.value));

watch(days, (list) => {
	const today = list.findIndex((day) => day.is_today || day.date === todayISO);
	selectedIndex.value = today > -1 ? today : 0;
});

const selected = computed(() => days.value[selectedIndex.value] ?? null);
const isThisWeek = computed(() => weekStart.value === null);

function shiftWeek(offset: number) {
	const base = parseDate(menu.data.value?.week_start) ?? siteNow(siteTimeZone.value);
	base.setDate(base.getDate() + offset * 7);
	const next = toISODate(base);
	const current = siteNow(siteTimeZone.value);
	const mondayOffset = (current.getDay() + 6) % 7;
	current.setDate(current.getDate() - mondayOffset);
	weekStart.value = next === toISODate(current) ? null : next;
}

function onTabKey(event: KeyboardEvent, index: number) {
	const count = days.value.length;
	let next = index;
	if (event.key === "ArrowRight") next = (index + 1) % count;
	else if (event.key === "ArrowLeft") next = (index - 1 + count) % count;
	else if (event.key === "Home") next = 0;
	else if (event.key === "End") next = count - 1;
	else return;
	event.preventDefault();
	selectedIndex.value = next;
	void nextTick(() => tabs.value[next]?.focus());
}

function shortDay(day: string): string {
	return tv("daysShort", day);
}
</script>

<template>
	<div>
		<PageHeader
			:title="t('menu.title')"
			:subtitle="menu.data.value ? formatDateRange(menu.data.value.week_start, menu.data.value.week_end) : undefined"
		/>

		<nav class="mb-4 flex flex-wrap gap-2" :aria-label="t('menu.related')">
			<RouterLink
				:to="{ name: 'mess-bills' }"
				class="inline-flex min-h-10 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-sm font-medium text-ink no-underline"
			>
				<UtensilsCrossed :size="16" aria-hidden="true" />{{ t("nav.messBills") }}
			</RouterLink>
			<RouterLink
				:to="{ name: 'mess-reduction' }"
				class="inline-flex min-h-10 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-sm font-medium text-ink no-underline"
			>
				<CalendarOff :size="16" aria-hidden="true" />{{ t("nav.messReduction") }}
			</RouterLink>
		</nav>

		<div class="mb-4 flex items-center justify-between gap-2">
			<button type="button" class="btn-secondary min-h-10 px-3 text-sm" @click="shiftWeek(-1)">
				<ChevronLeft :size="18" aria-hidden="true" />{{ t("menu.previousWeek") }}
			</button>
			<button v-if="!isThisWeek" type="button" class="btn-ghost min-h-10 px-3 text-sm" @click="weekStart = null">
				{{ t("menu.thisWeek") }}
			</button>
			<button type="button" class="btn-secondary min-h-10 px-3 text-sm" @click="shiftWeek(1)">
				{{ t("menu.nextWeek") }}<ChevronRight :size="18" aria-hidden="true" />
			</button>
		</div>

		<SkeletonList v-if="menu.loading.value && !menu.data.value" card :rows="2" />
		<ErrorState v-else-if="menu.error.value" :error="menu.error.value" @retry="menu.reload" />
		<EmptyState
			v-else-if="!menu.data.value?.mess"
			:icon="Utensils"
			:title="t('menu.noMess')"
			:body="t('menu.noMessHint')"
		/>
		<EmptyState
			v-else-if="!days.length"
			:icon="Utensils"
			:title="t('menu.notPublished')"
			:body="t('menu.notPublishedHint')"
		/>

		<template v-else>
			<div role="tablist" :aria-label="t('menu.daysLabel')" class="mb-4 grid grid-cols-7 gap-1 xs:gap-1.5">
				<button
					v-for="(day, index) in days"
					:id="`menu-tab-${index}`"
					:key="day.date"
					:ref="
						(el) => {
							if (el) tabs[index] = el as HTMLButtonElement;
						}
					"
					type="button"
					role="tab"
					class="flex min-h-14 min-w-0 flex-col items-center justify-center rounded-xl border px-0.5 text-[0.8rem] font-semibold transition-colors xs:text-sm"
					:class="index === selectedIndex ? 'border-brand bg-brand text-on-brand' : 'border-line bg-surface text-ink'"
					:aria-selected="index === selectedIndex ? 'true' : 'false'"
					:aria-controls="`menu-panel-${index}`"
					:tabindex="index === selectedIndex ? 0 : -1"
					@click="selectedIndex = index"
					@keydown="onTabKey($event, index)"
				>
					<span>{{ shortDay(day.day) }}</span>
					<span class="text-xs font-medium" :class="index === selectedIndex ? '' : 'text-muted'">{{
						day.date.slice(8, 10)
					}}</span>
					<span v-if="day.is_today || day.date === todayISO" class="sr-only">{{ t("menu.today") }}</span>
					<span
						v-if="day.is_today || day.date === todayISO"
						class="mt-0.5 size-1.5 rounded-full"
						:class="index === selectedIndex ? 'bg-white' : 'bg-accent'"
						aria-hidden="true"
					/>
				</button>
			</div>

			<section
				v-if="selected"
				:id="`menu-panel-${selectedIndex}`"
				role="tabpanel"
				:aria-labelledby="`menu-tab-${selectedIndex}`"
				class="card"
			>
				<h2 class="flex flex-wrap items-center gap-2 text-lg font-semibold text-ink">
					{{ tv("days", selected.day) }}, {{ formatDate(selected.date) }}
					<span v-if="selected.is_today || selected.date === todayISO" class="chip tone-info">{{
						t("menu.today")
					}}</span>
				</h2>
				<ul v-if="selected.meals.length" class="list-divided mt-2">
					<li v-for="meal in selected.meals" :key="meal.meal" class="py-3">
						<p class="flex items-center gap-2 text-sm font-semibold text-brand-ink">
							{{ tv("meals", meal.meal) }}
							<span v-if="meal.is_special" class="chip tone-success">{{ t("menu.special") }}</span>
						</p>
						<p class="mt-0.5 whitespace-pre-line text-[0.95rem] text-ink">{{ meal.dishes }}</p>
					</li>
				</ul>
				<p v-else class="mt-2 text-sm text-muted">{{ t("menu.noMeals") }}</p>
			</section>
			<p v-if="menu.data.value?.notes" class="mt-3 text-sm text-muted">{{ menu.data.value.notes }}</p>
		</template>
	</div>
</template>
