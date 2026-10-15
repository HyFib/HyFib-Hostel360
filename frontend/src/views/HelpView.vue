<script setup lang="ts">
import { ChevronDown, LifeBuoy, Mail, MessageSquareWarning, Phone } from "@lucide/vue";
import { computed, ref } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import PublicHeader from "@/components/PublicHeader.vue";
import { t } from "@/i18n";
import { session } from "@/stores/session";
import { readBoot } from "@/utils/boot";

const boot = readBoot();
const officePhone = computed(() => session.settings.office_phone || "");
const officeEmail = computed(() => session.settings.office_email || "");
const supportPhone = computed(() => session.settings.support_phone || boot.support_phone || "+91 96060 60596");
const supportEmail = computed(() => session.settings.support_email || boot.support_email || "support@hyfib.com");
const faqs = ["otp", "wrongNumber", "payment", "receipt", "leave", "confidential", "language"] as const;
const open = ref<string | null>(null);

const tel = (value: string) => `tel:${value.replace(/[^\d+]/g, "")}`;
</script>

<template>
	<div>
		<PublicHeader v-if="!session.loggedIn" />
		<div :class="session.loggedIn ? '' : 'mx-auto max-w-3xl px-4 py-6'">
			<PageHeader :title="t('help.title')" :subtitle="t('help.intro')" :back="session.loggedIn ? undefined : { name: 'login' }" />

			<div class="grid gap-4 sm:grid-cols-2">
				<section v-if="officePhone || officeEmail" class="card" aria-labelledby="help-office">
					<h2 id="help-office" class="section-title">{{ t("help.hostelOffice") }}</h2>
					<p class="mt-1 text-sm text-muted">{{ t("help.hostelOfficeBody") }}</p>
					<div class="mt-3 grid gap-2">
						<a v-if="officePhone" :href="tel(officePhone)" class="btn-secondary justify-start">
							<Phone :size="18" aria-hidden="true" />{{ officePhone }}
						</a>
						<a v-if="officeEmail" :href="`mailto:${officeEmail}`" class="btn-secondary justify-start">
							<Mail :size="18" aria-hidden="true" /><span class="truncate">{{ officeEmail }}</span>
						</a>
					</div>
				</section>

				<section class="card" aria-labelledby="help-support">
					<h2 id="help-support" class="flex items-center gap-2 section-title">
						<LifeBuoy :size="18" aria-hidden="true" />{{ t("help.portalSupport") }}
					</h2>
					<p class="mt-1 text-sm text-muted">{{ t("help.portalSupportBody") }}</p>
					<div class="mt-3 grid gap-2">
						<a :href="tel(supportPhone)" class="btn-secondary justify-start">
							<Phone :size="18" aria-hidden="true" />{{ supportPhone }}
						</a>
						<a :href="`mailto:${supportEmail}`" class="btn-secondary justify-start">
							<Mail :size="18" aria-hidden="true" /><span class="truncate">{{ supportEmail }}</span>
						</a>
					</div>
				</section>

				<section v-if="session.loggedIn" class="card sm:col-span-2" aria-labelledby="help-hostel">
					<h2 id="help-hostel" class="section-title">{{ t("help.hostelIssues") }}</h2>
					<p class="mt-1 text-sm text-muted">{{ t("help.hostelIssuesBody") }}</p>
					<RouterLink :to="{ name: 'grievance-new' }" class="btn-primary mt-3">
						<MessageSquareWarning :size="18" aria-hidden="true" />{{ t("grievances.raise") }}
					</RouterLink>
				</section>
			</div>

			<section class="card mt-4" aria-labelledby="help-ticket">
				<h2 id="help-ticket" class="section-title">{{ t("help.ticketTitle") }}</h2>
				<ul class="mt-2 list-disc space-y-1 pl-5 text-sm text-ink">
					<li>{{ t("help.ticket1") }}</li>
					<li>{{ t("help.ticket2") }}</li>
					<li>{{ t("help.ticket3") }}</li>
				</ul>
				<p class="mt-3 rounded-xl bg-warning-soft p-3 text-sm font-medium text-warning">{{ t("help.neverShare") }}</p>
			</section>

			<section class="mt-6" aria-labelledby="help-faq">
				<h2 id="help-faq" class="section-title mb-3">{{ t("help.faqTitle") }}</h2>
				<ul class="card list-divided p-0">
					<li v-for="faq in faqs" :key="faq">
						<h3>
							<button
								type="button"
								class="flex min-h-12 w-full items-center justify-between gap-3 px-4 py-3 text-left text-[0.95rem] font-medium text-ink"
								:aria-expanded="open === faq ? 'true' : 'false'"
								:aria-controls="`faq-${faq}`"
								@click="open = open === faq ? null : faq"
							>
								{{ t(`help.faq.${faq}.q`) }}
								<ChevronDown :size="18" class="shrink-0 text-muted transition-transform" :class="open === faq ? 'rotate-180' : ''" aria-hidden="true" />
							</button>
						</h3>
						<p v-if="open === faq" :id="`faq-${faq}`" class="px-4 pb-4 text-sm text-muted">{{ t(`help.faq.${faq}.a`) }}</p>
					</li>
				</ul>
			</section>
		</div>
	</div>
</template>
