<script setup lang="ts">
/** Parents with more than one ward in the hostel choose whose records they are viewing. */
import { Check, ChevronDown, Users } from "@lucide/vue";
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { Ward } from "@/api/types";
import { t, tv } from "@/i18n";
import { ACTIVE_STATUS, useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";
import BaseDialog from "./BaseDialog.vue";

const route = useRoute();
const router = useRouter();
const { session, activeWard, setActiveStudent } = useSession();
const open = ref(false);

/** "UAT24CS0012 · Boys Hostel 1 · A-103": names, not internal codes. */
function wardLine(ward: Ward): string {
	return [ward.register_number, ward.hostel_name || ward.hostel, ward.room_number || ward.room]
		.filter(Boolean)
		.join(" · ");
}

/** What the hostel office still has to do before this ward's pages open, if anything. */
function wardNotice(ward: Ward): string {
	if (ward.validation_status && ward.validation_status !== "Validated") {
		return ward.validation_status === "Rejected" ? t("wards.notValidated") : t("wards.pendingValidation");
	}
	if (ward.status && ward.status !== ACTIVE_STATUS) return tv("status", ward.status);
	return "";
}

function choose(ward: Ward) {
	open.value = false;
	if (ward.student === session.activeStudent) return;
	setActiveStudent(ward.student);
	showToast(t("wards.switched", { name: ward.full_name }), { tone: "info" });
	// Route guards do not run again on a ward switch; leave a page this ward cannot use.
	const meta = route.meta;
	const validated = !ward.validation_status || ward.validation_status === "Validated";
	const active = !ward.status || ward.status === ACTIVE_STATUS;
	if (meta.requiresValidated && !validated) {
		void router.replace({ name: "home", query: { notice: "verification" } });
	} else if (meta.requiresValidated && !meta.dues && !active) {
		void router.replace({ name: "home", query: { notice: "restricted" } });
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
						@click="choose(ward)"
					>
						<span
							class="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-brand-soft font-semibold text-brand-ink"
							aria-hidden="true"
						>
							{{ ward.full_name.charAt(0) }}
						</span>
						<span class="min-w-0 flex-1">
							<span class="block truncate font-semibold text-ink">{{ ward.full_name }}</span>
							<span class="block truncate text-sm text-muted">{{ wardLine(ward) }}</span>
							<span v-if="wardNotice(ward)" class="chip tone-warning mt-1">{{ wardNotice(ward) }}</span>
						</span>
						<Check v-if="ward.student === session.activeStudent" :size="20" class="text-brand-ink" aria-hidden="true" />
					</button>
				</li>
			</ul>
		</BaseDialog>
	</template>
</template>
