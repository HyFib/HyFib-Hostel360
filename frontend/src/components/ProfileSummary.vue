<script setup lang="ts">
/** Read-only details held by the hostel office (name, programme, room, guardians). */
import type { Profile } from "@/api/types";
import { t, tv } from "@/i18n";
import { formatDate } from "@/utils/format";

defineProps<{ profile: Profile }>();
</script>

<template>
	<div class="space-y-4">
		<section class="card" aria-labelledby="ps-personal">
			<h2 id="ps-personal" class="section-title mb-3">{{ t("profile.personal") }}</h2>
			<dl class="grid gap-3 text-sm sm:grid-cols-2">
				<div>
					<dt class="text-muted">{{ t("fields.full_name") }}</dt>
					<dd class="font-medium text-ink">{{ profile.full_name || "—" }}</dd>
				</div>
				<div>
					<dt class="text-muted">{{ t("fields.register_number") }}</dt>
					<dd class="font-medium text-ink">{{ profile.register_number || profile.name }}</dd>
				</div>
				<div v-if="profile.program">
					<dt class="text-muted">{{ t("fields.program") }}</dt>
					<dd class="font-medium text-ink">{{ profile.program }}</dd>
				</div>
				<div v-if="profile.year_of_study">
					<dt class="text-muted">{{ t("fields.year_of_study") }}</dt>
					<dd class="font-medium text-ink">{{ profile.year_of_study }}</dd>
				</div>
				<div v-if="profile.date_of_birth">
					<dt class="text-muted">{{ t("fields.date_of_birth") }}</dt>
					<dd class="font-medium text-ink">{{ formatDate(profile.date_of_birth) }}</dd>
				</div>
				<div v-if="profile.mobile_masked">
					<dt class="text-muted">{{ t("fields.mobile") }}</dt>
					<dd class="font-medium text-ink">{{ profile.mobile_masked }}</dd>
				</div>
			</dl>
		</section>

		<section
			v-if="profile.room && (profile.room.room || profile.room.room_number)"
			class="card"
			aria-labelledby="ps-room"
		>
			<h2 id="ps-room" class="section-title mb-3">{{ t("profile.room") }}</h2>
			<dl class="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
				<div>
					<dt class="text-muted">{{ t("fields.hostel") }}</dt>
					<dd class="font-medium text-ink">{{ profile.room.hostel_name || profile.room.hostel }}</dd>
				</div>
				<div v-if="profile.room.block_name || profile.room.block">
					<dt class="text-muted">{{ t("fields.block") }}</dt>
					<dd class="font-medium text-ink">{{ profile.room.block_name || profile.room.block }}</dd>
				</div>
				<div>
					<dt class="text-muted">{{ t("fields.room") }}</dt>
					<dd class="font-medium text-ink">{{ profile.room.room_number || profile.room.room }}</dd>
				</div>
				<div>
					<dt class="text-muted">{{ t("fields.bed") }}</dt>
					<dd class="font-medium text-ink">{{ profile.room.bed_label || profile.room.bed || "—" }}</dd>
				</div>
			</dl>
		</section>

		<section v-if="profile.guardians?.length" class="card" aria-labelledby="ps-guardians">
			<h2 id="ps-guardians" class="section-title mb-2">{{ t("profile.guardians") }}</h2>
			<ul class="list-divided">
				<li
					v-for="guardian in profile.guardians"
					:key="guardian.guardian"
					class="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm"
				>
					<span>
						<span class="font-medium text-ink">{{ guardian.guardian_name }}</span>
						<span v-if="guardian.relation" class="text-muted"> · {{ tv("relations", guardian.relation) }}</span>
					</span>
					<span class="flex items-center gap-2 text-muted">
						{{ guardian.mobile_masked }}
						<span v-if="guardian.is_primary" class="chip tone-info">{{ t("profile.primary") }}</span>
					</span>
				</li>
			</ul>
		</section>
	</div>
</template>
