<script setup lang="ts">
import { MapPin } from "@lucide/vue";
import EmptyState from "@/components/EmptyState.vue";
import { t } from "@/i18n";
import { session } from "@/stores/session";
</script>

<template>
	<div :class="session.loggedIn ? '' : 'mx-auto max-w-md px-4 py-16'">
		<h1 class="sr-only" tabindex="-1" data-page-title>{{ t("notFound.title") }}</h1>
		<EmptyState :icon="MapPin" :title="t('notFound.title')" :body="t('notFound.body')">
			<RouterLink :to="{ name: session.loggedIn ? 'home' : 'login' }" class="btn-primary">
				{{ session.loggedIn ? t("notFound.home") : t("notFound.login") }}
			</RouterLink>
		</EmptyState>
	</div>
</template>
