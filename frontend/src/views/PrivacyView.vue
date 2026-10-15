<script setup lang="ts">
/**
 * Privacy notice (DPDP Act 2023). Signed-in users who have not accepted the current version
 * are sent here first and accept it before anything else is shown.
 */
import { CircleAlert, ShieldCheck } from "@lucide/vue";
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { acceptPrivacyNotice } from "@/api/portal";
import PageHeader from "@/components/PageHeader.vue";
import PublicHeader from "@/components/PublicHeader.vue";
import { useSubmit } from "@/composables/useSubmit";
import { t } from "@/i18n";
import { safeRedirect } from "@/router/guards";
import { markConsentAccepted, session } from "@/stores/session";
import { showToast } from "@/stores/toast";
import { sanitizeHtml } from "@/utils/sanitize";

const route = useRoute();
const router = useRouter();
const { busy, error, run } = useSubmit();

const notice = computed(() => sanitizeHtml(session.privacyNotice));
const sections = ["collect", "use", "share", "keep", "rights", "contact"] as const;

async function accept() {
	const version = session.privacyNoticeVersion;
	const ok = await run(() => acceptPrivacyNotice(version));
	if (ok) {
		markConsentAccepted(version);
		showToast(t("privacy.accepted"));
		await router.replace(safeRedirect(route.query.redirect) ?? { name: "home" });
	}
}
</script>

<template>
	<div>
		<PublicHeader v-if="!session.loggedIn" />
		<div :class="session.loggedIn ? '' : 'mx-auto max-w-3xl px-4 py-6'">
			<PageHeader
				:title="t('privacy.title')"
				:subtitle="session.privacyNoticeVersion ? t('privacy.version', { version: session.privacyNoticeVersion }) : t('privacy.intro')"
				:back="session.loggedIn ? undefined : { name: 'login' }"
			/>

			<div v-if="session.consentRequired" class="card mb-4 flex items-start gap-3 border-brand bg-brand-soft" role="status">
				<ShieldCheck :size="22" class="mt-0.5 shrink-0 text-brand-ink" aria-hidden="true" />
				<p class="text-sm text-ink">{{ t("privacy.required") }}</p>
			</div>

			<!-- Administrator-written HTML, reduced to an allow-list of formatting tags by sanitizeHtml -->
			<article v-if="notice" class="card prose-notice text-[0.95rem] text-ink" v-html="notice" />
			<article v-else class="card prose-notice text-[0.95rem] text-ink">
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
				<button type="button" class="btn-primary btn-block min-h-12 shadow-lg" :disabled="busy" @click="accept">
					{{ busy ? t("common.saving") : t("privacy.accept") }}
				</button>
			</div>
		</div>
	</div>
</template>
