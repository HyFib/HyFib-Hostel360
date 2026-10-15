<script setup lang="ts">
/** First sign-in for a student: check what the office has on record and confirm it. */
import { ShieldCheck } from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";
import { confirmProfile, getProfile } from "@/api/portal";
import type { ProfileUpdates } from "@/api/types";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import ProfileForm from "@/components/ProfileForm.vue";
import ProfileSummary from "@/components/ProfileSummary.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { useResource } from "@/composables/useResource";
import { useSubmit } from "@/composables/useSubmit";
import { t } from "@/i18n";
import { safeRedirect } from "@/router/guards";
import { markProfileConfirmed } from "@/stores/session";
import { showToast } from "@/stores/toast";

const route = useRoute();
const router = useRouter();
const profile = useResource(() => getProfile());
const { busy, error, run } = useSubmit();

async function confirmDetails(updates: ProfileUpdates) {
	const ok = await run(() => confirmProfile(updates));
	if (ok) {
		markProfileConfirmed();
		showToast(t("confirm.saved"));
		await router.replace(safeRedirect(route.query.redirect) ?? { name: "home" });
	}
}
</script>

<template>
	<div>
		<PageHeader :title="t('confirm.title')" :subtitle="t('confirm.intro')" />

		<SkeletonList v-if="profile.loading.value && !profile.data.value" card :rows="2" />
		<ErrorState v-else-if="profile.error.value" :error="profile.error.value" @retry="profile.reload" />

		<template v-else-if="profile.data.value">
			<ProfileSummary :profile="profile.data.value" />
			<p class="mt-3 mb-4 flex items-start gap-2 text-sm text-muted">
				<ShieldCheck :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />{{ t("confirm.readonlyNote") }}
			</p>
			<section class="card">
				<ProfileForm
					:profile="profile.data.value"
					:editable="profile.data.value.editable_fields"
					:busy="busy"
					:server-error="error"
					:submit-label="t('confirm.confirm')"
					@submit="confirmDetails"
				/>
			</section>
		</template>
	</div>
</template>
