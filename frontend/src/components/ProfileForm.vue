<script setup lang="ts">
/** The contact and address details a student may correct themselves (design §5 confirm_profile). */
import { CircleAlert } from "@lucide/vue";
import { nextTick, reactive, ref, watch } from "vue";
import { PROFILE_EDITABLE_FIELDS, type EditableField, type Profile, type ProfileUpdates } from "@/api/types";
import { BLOOD_GROUPS } from "@/constants";
import { t } from "@/i18n";
import { normalizeMobile } from "@/utils/format";
import FormField from "./FormField.vue";

const props = defineProps<{
	profile: Profile;
	busy?: boolean;
	serverError?: string;
	submitLabel: string;
	editable?: string[];
	cancellable?: boolean;
}>();
const emit = defineEmits<{ submit: [updates: ProfileUpdates]; cancel: [] }>();

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const form = reactive<Record<EditableField, string>>(
	Object.fromEntries(PROFILE_EDITABLE_FIELDS.map((field) => [field, ""])) as Record<EditableField, string>,
);
const errors = reactive<Partial<Record<EditableField, string>>>({});
const root = ref<HTMLFormElement | null>(null);

function load(profile: Profile) {
	for (const field of PROFILE_EDITABLE_FIELDS) {
		const value = profile[field];
		// Portal accounts without an e-mail use a placeholder address that must never be shown.
		form[field] = field === "email" && String(value || "").endsWith(".invalid") ? "" : String(value ?? "");
	}
}
watch(() => props.profile, load, { immediate: true });

const allowed = (field: EditableField) => !props.editable || props.editable.includes(field);

function validate(): boolean {
	for (const key of Object.keys(errors) as EditableField[]) delete errors[key];
	if (form.email.trim() && !EMAIL.test(form.email.trim())) errors.email = t("validation.email");
	if (form.pincode.trim() && !/^\d{6}$/.test(form.pincode.trim())) errors.pincode = t("validation.pincode");
	if (form.emergency_contact_phone.trim() && !normalizeMobile(form.emergency_contact_phone)) {
		errors.emergency_contact_phone = t("validation.phone");
	}
	if (!form.address_line1.trim()) errors.address_line1 = t("validation.required", { field: t("fields.address_line1") });
	if (!form.city.trim()) errors.city = t("validation.required", { field: t("fields.city") });
	if (!form.emergency_contact_name.trim()) {
		errors.emergency_contact_name = t("validation.required", { field: t("fields.emergency_contact_name") });
	}
	if (!form.emergency_contact_phone.trim()) {
		errors.emergency_contact_phone = t("validation.required", { field: t("fields.emergency_contact_phone") });
	}
	return Object.keys(errors).length === 0;
}

async function submit() {
	if (!validate()) {
		await nextTick();
		root.value?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
		return;
	}
	const updates: ProfileUpdates = {};
	for (const field of PROFILE_EDITABLE_FIELDS) {
		if (!allowed(field)) continue;
		let value = form[field].trim();
		if (field === "emergency_contact_phone") value = normalizeMobile(value) || value;
		updates[field] = value;
	}
	emit("submit", updates);
}
</script>

<template>
	<form ref="root" novalidate @submit.prevent="submit">
		<h2 class="section-title mb-3">{{ t("profile.contact") }}</h2>
		<FormField v-if="allowed('email')" v-slot="{ id, describedBy, invalid }" :label="t('fields.email')" :error="errors.email">
			<input :id="id" v-model="form.email" class="input" type="email" autocomplete="email" inputmode="email" maxlength="140" :aria-describedby="describedBy" :aria-invalid="invalid">
		</FormField>

		<h2 class="section-title mt-6 mb-3">{{ t("profile.address") }}</h2>
		<FormField v-if="allowed('address_line1')" v-slot="{ id, describedBy, invalid }" :label="t('fields.address_line1')" :error="errors.address_line1" required>
			<input :id="id" v-model="form.address_line1" class="input" type="text" autocomplete="address-line1" maxlength="140" :aria-describedby="describedBy" :aria-invalid="invalid">
		</FormField>
		<FormField v-if="allowed('address_line2')" v-slot="{ id, describedBy }" :label="t('fields.address_line2')">
			<input :id="id" v-model="form.address_line2" class="input" type="text" autocomplete="address-line2" maxlength="140" :aria-describedby="describedBy">
		</FormField>
		<div class="grid gap-x-4 sm:grid-cols-2">
			<FormField v-if="allowed('city')" v-slot="{ id, describedBy, invalid }" :label="t('fields.city')" :error="errors.city" required>
				<input :id="id" v-model="form.city" class="input" type="text" autocomplete="address-level2" maxlength="80" :aria-describedby="describedBy" :aria-invalid="invalid">
			</FormField>
			<FormField v-if="allowed('district')" v-slot="{ id, describedBy }" :label="t('fields.district')">
				<input :id="id" v-model="form.district" class="input" type="text" maxlength="80" :aria-describedby="describedBy">
			</FormField>
			<FormField v-if="allowed('state')" v-slot="{ id, describedBy }" :label="t('fields.state')">
				<input :id="id" v-model="form.state" class="input" type="text" autocomplete="address-level1" maxlength="80" :aria-describedby="describedBy">
			</FormField>
			<FormField v-if="allowed('pincode')" v-slot="{ id, describedBy, invalid }" :label="t('fields.pincode')" :error="errors.pincode">
				<input :id="id" v-model="form.pincode" class="input" type="text" inputmode="numeric" autocomplete="postal-code" maxlength="6" :aria-describedby="describedBy" :aria-invalid="invalid">
			</FormField>
		</div>

		<h2 class="section-title mt-6 mb-3">{{ t("profile.emergency") }}</h2>
		<div class="grid gap-x-4 sm:grid-cols-2">
			<FormField v-if="allowed('emergency_contact_name')" v-slot="{ id, describedBy, invalid }" :label="t('fields.emergency_contact_name')" :error="errors.emergency_contact_name" required>
				<input :id="id" v-model="form.emergency_contact_name" class="input" type="text" maxlength="140" :aria-describedby="describedBy" :aria-invalid="invalid">
			</FormField>
			<FormField v-if="allowed('emergency_contact_phone')" v-slot="{ id, describedBy, invalid }" :label="t('fields.emergency_contact_phone')" :error="errors.emergency_contact_phone" required>
				<input :id="id" v-model="form.emergency_contact_phone" class="input" type="tel" inputmode="numeric" maxlength="14" :aria-describedby="describedBy" :aria-invalid="invalid">
			</FormField>
		</div>
		<FormField v-if="allowed('blood_group')" v-slot="{ id, describedBy }" :label="t('fields.blood_group')">
			<select :id="id" v-model="form.blood_group" class="input" :aria-describedby="describedBy">
				<option value="">{{ t("common.notSet") }}</option>
				<option v-for="group in BLOOD_GROUPS" :key="group" :value="group">{{ group }}</option>
			</select>
		</FormField>

		<p v-if="serverError" class="mb-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
			<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ serverError }}
		</p>
		<div class="mt-2 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
			<button v-if="cancellable" type="button" class="btn-secondary" @click="emit('cancel')">{{ t("common.cancel") }}</button>
			<button type="submit" class="btn-primary" :disabled="busy">{{ busy ? t("common.saving") : submitLabel }}</button>
		</div>
	</form>
</template>
