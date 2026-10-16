<script setup lang="ts">
import { LogOut, Pencil } from "@lucide/vue";
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { confirmProfile, getProfile } from "@/api/portal";
import type { ProfileUpdates } from "@/api/types";
import ErrorState from "@/components/ErrorState.vue";
import LanguageSwitch from "@/components/LanguageSwitch.vue";
import PageHeader from "@/components/PageHeader.vue";
import ProfileForm from "@/components/ProfileForm.vue";
import ProfileSummary from "@/components/ProfileSummary.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { confirm } from "@/composables/useConfirm";
import { useResource } from "@/composables/useResource";
import { useSubmit } from "@/composables/useSubmit";
import { t } from "@/i18n";
import { resetInbox } from "@/stores/inbox";
import { studentArg, useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";

const router = useRouter();
const { session, isParent, isStudent, signOut } = useSession();
const profile = useResource(() => getProfile(studentArg()), { watch: [() => session.activeStudent] });
const editing = ref(false);
const { busy, error, run } = useSubmit();

const contactRows = computed(() => {
	const data = profile.data.value;
	if (!data) return [];
	const address = [data.address_line1, data.address_line2, data.city, data.district, data.state, data.pincode]
		.filter(Boolean)
		.join(", ");
	const email = data.email && !String(data.email).endsWith(".invalid") ? data.email : "";
	return [
		{ label: t("fields.email"), value: email },
		{ label: t("profile.address"), value: address },
		{ label: t("fields.emergency_contact_name"), value: data.emergency_contact_name },
		{ label: t("fields.emergency_contact_phone"), value: data.emergency_contact_phone },
		{ label: t("fields.blood_group"), value: data.blood_group },
	];
});

async function save(updates: ProfileUpdates) {
	const ok = await run(() => confirmProfile(updates));
	if (ok) {
		editing.value = false;
		showToast(t("profile.saved"));
		await profile.reload();
	}
}

async function logout() {
	const ok = await confirm({
		title: t("profile.logoutTitle"),
		message: t("profile.logoutConfirm"),
		confirmLabel: t("profile.logout"),
	});
	if (!ok) return;
	await signOut();
	resetInbox();
	await router.replace({ name: "login" });
}
</script>

<template>
	<div>
		<PageHeader :title="t('profile.title')" :subtitle="t('profile.signedInAs', { name: session.displayName })" />

		<section class="card mb-4" aria-labelledby="pf-account">
			<h2 id="pf-account" class="section-title mb-3">{{ t("profile.account") }}</h2>
			<dl class="grid gap-3 text-sm sm:grid-cols-2">
				<div>
					<dt class="text-muted">{{ t("profile.signedInAsLabel") }}</dt>
					<dd class="font-medium text-ink">
						{{ session.displayName }} · {{ isParent ? t("profile.kindParent") : t("profile.kindStudent") }}
					</dd>
				</div>
				<div v-if="session.mobileMasked">
					<dt class="text-muted">{{ t("fields.mobile") }}</dt>
					<dd class="font-medium text-ink">{{ session.mobileMasked }}</dd>
				</div>
				<div v-if="isParent && session.wards.length" class="sm:col-span-2">
					<dt class="text-muted">{{ t("profile.wards") }}</dt>
					<dd class="font-medium text-ink">{{ session.wards.map((ward) => ward.full_name).join(", ") }}</dd>
				</div>
			</dl>
			<div class="mt-4">
				<p class="field-label">{{ t("profile.language") }}</p>
				<LanguageSwitch />
			</div>
		</section>

		<SkeletonList v-if="profile.loading.value && !profile.data.value" card :rows="1" />
		<ErrorState v-else-if="profile.error.value" :error="profile.error.value" @retry="profile.reload" />

		<template v-else-if="profile.data.value">
			<p v-if="isParent" class="mb-2 text-sm font-semibold text-muted">
				{{ t("profile.wardDetails", { name: profile.data.value.full_name || "" }) }}
			</p>
			<ProfileSummary :profile="profile.data.value" />

			<section class="card mt-4" aria-labelledby="pf-contact">
				<div class="mb-3 flex items-center justify-between gap-2">
					<h2 id="pf-contact" class="section-title">{{ t("profile.contactAndAddress") }}</h2>
					<button
						v-if="isStudent && !editing"
						type="button"
						class="btn-ghost min-h-10 px-3 text-sm"
						@click="editing = true"
					>
						<Pencil :size="16" aria-hidden="true" />{{ t("profile.edit") }}
					</button>
				</div>
				<ProfileForm
					v-if="editing"
					:profile="profile.data.value"
					:editable="profile.data.value.editable_fields"
					:busy="busy"
					:server-error="error"
					:submit-label="t('profile.save')"
					cancellable
					@submit="save"
					@cancel="editing = false"
				/>
				<dl v-else class="grid gap-3 text-sm sm:grid-cols-2">
					<div v-for="row in contactRows" :key="row.label">
						<dt class="text-muted">{{ row.label }}</dt>
						<dd class="font-medium text-ink">{{ row.value || t("common.notSet") }}</dd>
					</div>
				</dl>
				<p class="mt-4 text-xs text-muted">{{ t("confirm.readonlyNote") }}</p>
			</section>
		</template>

		<button type="button" class="btn-danger btn-block mt-6" @click="logout">
			<LogOut :size="18" aria-hidden="true" />{{ t("profile.logout") }}
		</button>
	</div>
</template>
