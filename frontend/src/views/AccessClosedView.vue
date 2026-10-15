<script setup lang="ts">
/**
 * Shown when the server ends a session because the record may not use the portal now: the
 * registration is not validated yet, the record was rejected or closed, the account is no
 * longer linked to a hostel record, or the portal is switched off for a while.
 *
 * The server's own reason is shown as it was sent (it is already in the person's language),
 * next to the hostel office's contacts: only the office can change any of these, so a bare
 * sign-in form would only invite retries that are refused for the same reason.
 */
import { LifeBuoy, LogIn, Mail, Phone, ShieldAlert } from "@lucide/vue";
import { computed } from "vue";
import { useRouter } from "vue-router";
import PublicHeader from "@/components/PublicHeader.vue";
import { t } from "@/i18n";
import { clearClosedReason, useSession } from "@/stores/session";
import { readBoot } from "@/utils/boot";

const router = useRouter();
const { session } = useSession();
const boot = readBoot();
// The session is gone, so the office contacts come from the settings kept for guests or the page's boot data.
const officePhone = computed(() => session.settings.office_phone || boot.office_phone || "");
const officeEmail = computed(() => session.settings.office_email || boot.office_email || "");

const tel = (value: string) => `tel:${value.replace(/[^\d+]/g, "")}`;

async function signInAgain() {
	await router.replace({ name: "login" });
	// The reason has been read here; the sign-in page need not repeat it.
	clearClosedReason();
}
</script>

<template>
	<div class="min-h-dvh bg-bg">
		<PublicHeader />
		<main id="main" class="mx-auto w-full max-w-md px-4 py-8">
			<section class="card p-6 text-center" aria-labelledby="closed-title">
				<span class="mx-auto inline-flex size-16 items-center justify-center rounded-full bg-warning-soft text-warning" aria-hidden="true">
					<ShieldAlert :size="30" />
				</span>
				<h1 id="closed-title" class="mt-4 text-xl font-bold text-ink" tabindex="-1" data-page-title>{{ t("closed.title") }}</h1>
				<p class="mt-2 text-[0.95rem] text-ink" data-test="closed-reason">{{ session.closedReason }}</p>
				<p class="mt-3 text-sm text-muted">{{ t("closed.signedOut") }}</p>
			</section>

			<section v-if="officePhone || officeEmail" class="card mt-4" aria-labelledby="closed-office" data-test="closed-office">
				<h2 id="closed-office" class="section-title">{{ t("closed.officeTitle") }}</h2>
				<p class="mt-1 text-sm text-muted">{{ t("closed.officeBody") }}</p>
				<div class="mt-3 grid gap-2">
					<a v-if="officePhone" :href="tel(officePhone)" class="btn-secondary justify-start">
						<Phone :size="18" aria-hidden="true" />{{ officePhone }}
					</a>
					<a v-if="officeEmail" :href="`mailto:${officeEmail}`" class="btn-secondary justify-start">
						<Mail :size="18" aria-hidden="true" /><span class="truncate">{{ officeEmail }}</span>
					</a>
				</div>
			</section>
			<p v-else class="card mt-4 text-sm text-muted">{{ t("closed.officeFallback") }}</p>

			<button type="button" class="btn-primary btn-block mt-6 min-h-12" data-test="sign-in-again" @click="signInAgain">
				<LogIn :size="18" aria-hidden="true" />{{ t("closed.signInAgain") }}
			</button>
			<RouterLink :to="{ name: 'help' }" class="btn-ghost mt-2 w-full justify-center">
				<LifeBuoy :size="18" aria-hidden="true" />{{ t("closed.help") }}
			</RouterLink>
		</main>
	</div>
</template>
