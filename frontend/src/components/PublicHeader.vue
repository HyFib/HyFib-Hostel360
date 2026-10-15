<script setup lang="ts">
/** Top bar for pages visitors can open before signing in (help, privacy). */
import { t } from "@/i18n";
import { useSession } from "@/stores/session";
import InstitutionMark from "./InstitutionMark.vue";
import LanguageSwitch from "./LanguageSwitch.vue";

const { session, institutionName } = useSession();
</script>

<template>
	<header class="border-b border-line bg-surface">
		<div class="mx-auto flex min-h-16 max-w-3xl items-center justify-between gap-3 px-4 py-2">
			<RouterLink :to="{ name: 'login' }" class="flex min-w-0 items-center gap-2.5 no-underline">
				<InstitutionMark :logo="session.settings.logo" :name="institutionName" />
				<span class="truncate font-bold text-ink">{{ institutionName || t("app.name") }}</span>
			</RouterLink>
			<LanguageSwitch compact class="shrink-0" />
		</div>
	</header>
</template>
