<script setup lang="ts">
/**
 * Privacy notice (DPDP Act 2023). Signed-in users who have not accepted the current version
 * are sent here first and accept it before anything else is shown.
 *
 * The text is the institution's own notice from the server, and acceptance records the
 * version that was actually shown. The built-in summary is only for a site that has not
 * written a notice yet; a signed-in user can never accept while the real text is missing.
 */
import { CircleAlert, ShieldCheck } from "@lucide/vue";
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { errorMessage } from "@/api/client";
import { acceptPrivacyNotice, getPrivacyNotice } from "@/api/portal";
import type { PrivacyNotice } from "@/api/types";
import PageHeader from "@/components/PageHeader.vue";
import PublicHeader from "@/components/PublicHeader.vue";
import SkeletonList from "@/components/SkeletonList.vue";
import { useSubmit } from "@/composables/useSubmit";
import { t } from "@/i18n";
import { safeRedirect } from "@/router/guards";
import { markConsentAccepted, session } from "@/stores/session";
import { showToast } from "@/stores/toast";
import { sanitizeHtml } from "@/utils/sanitize";

const route = useRoute();
const router = useRouter();
const { busy, error, run } = useSubmit();

const loaded = ref<PrivacyNotice | null>(null);
const loading = ref(true);
const loadError = ref("");

const notice = computed(() => sanitizeHtml(loaded.value?.notice));
const version = computed(() => loaded.value?.version || "");
const sections = ["collect", "use", "share", "keep", "rights", "contact"] as const;
/** Accepting needs the notice the server holds; the version accepted is the one shown. */
const canAccept = computed(() => Boolean(loaded.value && version.value) && !loading.value);

async function load() {
	loading.value = true;
	loadError.value = "";
	try {
		loaded.value = await getPrivacyNotice();
	} catch (caught) {
		loadError.value = errorMessage(caught);
	} finally {
		loading.value = false;
	}
}

async function accept() {
	if (!canAccept.value) return;
	const accepted = version.value;
	const ok = await run(() => acceptPrivacyNotice(accepted));
	if (ok) {
		markConsentAccepted(accepted);
		showToast(t("privacy.accepted"));
		await router.replace(safeRedirect(route.query.redirect) ?? { name: "home" });
	}
}

onMounted(load);
</script>

<template>
	<div>
		<PublicHeader v-if="!session.loggedIn" />
		<div :class="session.loggedIn ? '' : 'mx-auto max-w-3xl px-4 py-6'">
			<PageHeader
				:title="t('privacy.title')"
				:subtitle="version ? t('privacy.version', { version }) : t('privacy.intro')"
				:back="session.loggedIn ? undefined : { name: 'login' }"
			/>

			<div v-if="session.consentRequired" class="card mb-4 flex items-start gap-3 border-brand bg-brand-soft" role="status">
				<ShieldCheck :size="22" class="mt-0.5 shrink-0 text-brand-ink" aria-hidden="true" />
				<p class="text-sm text-ink">{{ t("privacy.required") }}</p>
			</div>

			<SkeletonList v-if="loading" card :rows="2" />
			<div v-else-if="loadError" class="card mb-4" role="alert" data-test="notice-error">
				<p class="flex items-start gap-2 text-sm font-medium text-danger">
					<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ t("privacy.loadFailed") }}
				</p>
				<p class="mt-1 text-sm text-muted">{{ loadError }}</p>
				<button type="button" class="btn-secondary mt-3" @click="load">{{ t("common.retry") }}</button>
			</div>

			<!-- Administrator-written HTML, reduced to an allow-list of formatting tags by sanitizeHtml -->
			<article v-else-if="notice" class="card prose-notice text-[0.95rem] text-ink" data-test="notice" v-html="notice" />
			<article v-else class="card prose-notice text-[0.95rem] text-ink" data-test="notice-default">
				<p>{{ t("privacy.default.lead") }}</p>
				<template v-for="section in sections" :key="section">
					<h2>{{ t(`privacy.default.${section}Title`) }}</h2>
					<p>{{ t(`privacy.default.${section}`) }}</p>
				</template>
			</article>

			<div v-if="session.loggedIn && session.consentRequired" class="sticky bottom-4 mt-4">
				<p v-if="error" class="mb-2 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
					<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ error }}
				</p>
				<button
					type="button"
					class="btn-primary btn-block min-h-12 shadow-lg"
					:disabled="busy || !canAccept"
					data-test="accept"
					@click="accept"
				>
					{{ busy ? t("common.saving") : t("privacy.accept") }}
				</button>
			</div>
		</div>
	</div>
</template>
