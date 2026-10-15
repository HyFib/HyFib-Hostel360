<script setup lang="ts">
import { t } from "@/i18n";
import { confirmState, settleConfirm } from "@/composables/useConfirm";
import BaseDialog from "./BaseDialog.vue";
</script>

<template>
	<BaseDialog :open="confirmState.open" :title="confirmState.title" @close="settleConfirm(false)">
		<p v-if="confirmState.message" class="text-[0.95rem] text-muted">{{ confirmState.message }}</p>
		<div class="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
			<button type="button" class="btn-secondary" @click="settleConfirm(false)">
				{{ confirmState.cancelLabel || t("common.cancel") }}
			</button>
			<button
				type="button"
				data-autofocus
				:class="confirmState.danger ? 'btn-danger' : 'btn-primary'"
				@click="settleConfirm(true)"
			>
				{{ confirmState.confirmLabel || t("common.confirm") }}
			</button>
		</div>
	</BaseDialog>
</template>
