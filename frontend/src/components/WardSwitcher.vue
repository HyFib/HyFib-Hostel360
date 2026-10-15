<script setup lang="ts">
/** Parents with more than one ward in the hostel choose whose records they are viewing. */
import { Check, ChevronDown, Users } from "@lucide/vue";
import { ref } from "vue";
import { t } from "@/i18n";
import { useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";
import BaseDialog from "./BaseDialog.vue";

const { session, activeWard, setActiveStudent } = useSession();
const open = ref(false);

function choose(student: string, name: string) {
	open.value = false;
	if (student !== session.activeStudent) {
		setActiveStudent(student);
		showToast(t("wards.switched", { name }), { tone: "info" });
	}
}
</script>

<template>
	<template v-if="session.kind === 'parent' && activeWard">
		<button
			v-if="session.wards.length > 1"
			type="button"
			class="inline-flex min-h-11 max-w-[11rem] items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-sm font-semibold text-ink"
			:aria-label="t('wards.switchLabel', { name: activeWard.full_name })"
			@click="open = true"
		>
			<Users :size="16" aria-hidden="true" class="shrink-0" />
			<span class="truncate">{{ activeWard.full_name }}</span>
			<ChevronDown :size="16" aria-hidden="true" class="shrink-0" />
		</button>
		<span v-else class="inline-flex max-w-[11rem] items-center gap-1.5 truncate text-sm font-medium text-muted">
			<Users :size="16" aria-hidden="true" class="shrink-0" />
			<span class="truncate">{{ activeWard.full_name }}</span>
		</span>

		<BaseDialog :open="open" :title="t('wards.choose')" @close="open = false">
			<ul class="list-divided -mx-2">
				<li v-for="ward in session.wards" :key="ward.student">
					<button
						type="button"
						class="flex min-h-14 w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-surface-2"
						:aria-current="ward.student === session.activeStudent ? 'true' : undefined"
						:data-autofocus="ward.student === session.activeStudent ? '' : undefined"
						@click="choose(ward.student, ward.full_name)"
					>
						<span class="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-brand-soft font-semibold text-brand-ink" aria-hidden="true">
							{{ ward.full_name.charAt(0) }}
						</span>
						<span class="min-w-0 flex-1">
							<span class="block truncate font-semibold text-ink">{{ ward.full_name }}</span>
							<span class="block truncate text-sm text-muted">
								{{ [ward.register_number, ward.hostel, ward.room].filter(Boolean).join(" · ") }}
							</span>
						</span>
						<Check v-if="ward.student === session.activeStudent" :size="20" class="text-brand-ink" aria-hidden="true" />
					</button>
				</li>
			</ul>
		</BaseDialog>
	</template>
</template>
