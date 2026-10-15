<script setup lang="ts">
/**
 * OTP sign-in for students and parents: mobile number → code → (if the number belongs to
 * both a student and a parent) choose who is signing in.
 */
import { ArrowRight, CircleAlert, LifeBuoy, MessageCircle, ShieldCheck, Smartphone } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { sendOtp, verifyOtp } from "@/api/auth";
import { ApiError, errorMessage } from "@/api/client";
import type { OtpChannel, PortalKind } from "@/api/types";
import InstitutionMark from "@/components/InstitutionMark.vue";
import LanguageSwitch from "@/components/LanguageSwitch.vue";
import OtpInput from "@/components/OtpInput.vue";
import { t } from "@/i18n";
import { safeRedirect } from "@/router/guards";
import { clearClosedReason, completeSignIn, useSession } from "@/stores/session";
import { formatMobile, normalizeMobile } from "@/utils/format";
import { getItem, setItem } from "@/utils/storage";

const OTP_LENGTH = 6;
type Step = "mobile" | "code" | "choose";

const route = useRoute();
const router = useRouter();
const { session, institutionName } = useSession();

const preferredKind = computed<PortalKind | null>(() =>
	route.query.as === "parent" || route.query.as === "student" ? (route.query.as as PortalKind) : null,
);

const step = ref<Step>("mobile");
const mobileInput = ref("");
const mobile = ref("");
const channel = ref<OtpChannel>(getItem("otp-channel") === "whatsapp" ? "whatsapp" : "sms");
const code = ref("");
const masked = ref("");
const expiresIn = ref(300);
const resendIn = ref(0);
const busy = ref(false);
const error = ref("");
const fieldError = ref("");
const info = ref("");
const choices = ref<PortalKind[]>([]);

const otpRef = ref<InstanceType<typeof OtpInput> | null>(null);
const mobileField = ref<HTMLInputElement | null>(null);
let countdown: ReturnType<typeof setInterval> | null = null;
let webOtp: AbortController | null = null;

const title = computed(() =>
	preferredKind.value === "parent" ? t("login.parentTitle") : preferredKind.value === "student" ? t("login.studentTitle") : t("login.title"),
);
const channelLabel = computed(() => (channel.value === "whatsapp" ? t("login.whatsapp") : t("login.sms")));

function startCountdown(seconds: number) {
	resendIn.value = Math.max(0, Math.round(seconds));
	if (countdown) clearInterval(countdown);
	countdown = setInterval(() => {
		resendIn.value = Math.max(0, resendIn.value - 1);
		if (resendIn.value === 0 && countdown) {
			clearInterval(countdown);
			countdown = null;
		}
	}, 1000);
}

/** Android Chrome can read the code from the SMS directly (WebOTP); harmless elsewhere. */
function listenForSmsCode() {
	webOtp?.abort();
	if (channel.value !== "sms" || !("OTPCredential" in window) || !navigator.credentials?.get) return;
	webOtp = new AbortController();
	navigator.credentials
		.get({ otp: { transport: ["sms"] }, signal: webOtp.signal } as CredentialRequestOptions)
		.then((credential) => {
			const value = (credential as unknown as { code?: string } | null)?.code;
			if (value && step.value === "code") {
				code.value = value.replace(/\D/g, "").slice(0, OTP_LENGTH);
				if (code.value.length === OTP_LENGTH) void submitCode();
			}
		})
		.catch(() => undefined);
}

async function requestCode(isResend = false) {
	error.value = "";
	fieldError.value = "";
	info.value = "";
	const normalised = normalizeMobile(mobileInput.value);
	if (!normalised) {
		fieldError.value = t("login.mobileInvalid");
		await nextTick();
		mobileField.value?.focus();
		return;
	}
	busy.value = true;
	try {
		const result = await sendOtp(normalised, channel.value);
		mobile.value = normalised;
		masked.value = result.masked || formatMobile(normalised);
		expiresIn.value = result.expires_in || 300;
		startCountdown(result.resend_after || 60);
		setItem("otp-channel", channel.value);
		code.value = "";
		step.value = "code";
		if (isResend) info.value = t("login.resent");
		listenForSmsCode();
		await nextTick();
		otpRef.value?.clear();
	} catch (caught) {
		error.value = errorMessage(caught);
		const wait = /(\d+)\s*(seconds|second|வினாடி)/i.exec(error.value);
		if (caught instanceof ApiError && caught.kind === "rate_limit" && wait && step.value === "code") {
			startCountdown(Number(wait[1]));
		}
	} finally {
		busy.value = false;
	}
}

async function finishSignIn() {
	await completeSignIn();
	const target = safeRedirect(route.query.redirect) ?? "/";
	await router.replace(target);
}

async function submitCode(kind?: PortalKind) {
	if (busy.value) return;
	error.value = "";
	info.value = "";
	if (code.value.length !== OTP_LENGTH) {
		error.value = t("login.codeIncomplete", { length: OTP_LENGTH });
		otpRef.value?.focus();
		return;
	}
	busy.value = true;
	try {
		const result = await verifyOtp(mobile.value, code.value, kind);
		if (result.ok) {
			webOtp?.abort();
			await finishSignIn();
			return;
		}
		const options = (result.choose || []).filter((value): value is PortalKind => value === "student" || value === "parent");
		if (options.length > 1 && !kind) {
			if (preferredKind.value && options.includes(preferredKind.value)) {
				busy.value = false;
				await submitCode(preferredKind.value);
				return;
			}
			choices.value = options;
			step.value = "choose";
			return;
		}
		error.value = t("errors.generic");
	} catch (caught) {
		error.value = errorMessage(caught);
		// The choose step has no Resend or Change number: if the server refused the code
		// (expired, locked), go back to the code step, where both are, instead of letting
		// every tap resend a dead code. A dropped connection keeps the still-valid code.
		const retryable = caught instanceof ApiError && caught.isNetwork;
		if (step.value === "choose" && !retryable) {
			choices.value = [];
			step.value = "code";
		}
		if (step.value === "code") {
			code.value = "";
			otpRef.value?.clear();
		}
	} finally {
		busy.value = false;
	}
}

function changeNumber() {
	webOtp?.abort();
	choices.value = [];
	step.value = "mobile";
	code.value = "";
	error.value = "";
	info.value = "";
	void nextTick(() => mobileField.value?.focus());
}

watch(step, () => {
	void nextTick(() => document.querySelector<HTMLElement>("[data-step-title]")?.focus());
});

onBeforeUnmount(() => {
	if (countdown) clearInterval(countdown);
	webOtp?.abort();
});
</script>

<template>
	<div class="flex min-h-dvh flex-col bg-bg">
		<div class="bg-gradient-to-br from-[#083b75] via-[#0b4f9c] to-[#0e7490] px-4 pt-4 pb-20 text-white">
			<div class="mx-auto flex max-w-md items-center justify-between gap-3">
				<div class="flex min-w-0 items-center gap-2.5">
					<InstitutionMark :logo="session.settings.logo" :name="institutionName" :size="40" />
					<span class="truncate font-semibold">{{ institutionName || t("app.name") }}</span>
				</div>
			</div>
			<div class="mx-auto mt-8 max-w-md">
				<p class="text-sm font-medium text-cyan-100">{{ t("app.portal") }}</p>
				<h1 class="mt-1 text-[1.75rem] font-bold leading-tight" tabindex="-1" data-page-title>{{ title }}</h1>
				<p class="mt-2 text-[0.95rem] text-blue-50">{{ t("login.subtitle") }}</p>
			</div>
		</div>

		<main id="main" class="mx-auto -mt-14 w-full max-w-md flex-1 px-4 pb-10">
			<div
				v-if="session.closedReason && step === 'mobile'"
				class="card mb-4 flex items-start gap-3 border-warning bg-warning-soft"
				role="alert"
				data-test="closed-reason"
			>
				<CircleAlert :size="22" class="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
				<div class="min-w-0 flex-1">
					<p class="font-semibold text-ink">{{ t("login.closedTitle") }}</p>
					<p class="mt-0.5 text-sm text-muted">{{ session.closedReason }}</p>
					<button type="button" class="btn-ghost -ml-3 mt-1 min-h-10 px-3 text-sm" @click="clearClosedReason">{{ t("common.dismiss") }}</button>
				</div>
			</div>

			<section class="card p-5 sm:p-6" aria-live="off">
				<!-- Step 1: mobile number -->
				<form v-if="step === 'mobile'" novalidate @submit.prevent="requestCode()">
					<h2 class="sr-only" tabindex="-1" data-step-title>{{ t("login.mobileStep") }}</h2>
					<label for="login-mobile" class="field-label">{{ t("login.mobileLabel") }}</label>
					<div class="flex items-stretch gap-2">
						<span class="inline-flex min-h-12 items-center rounded-xl border border-line bg-surface-2 px-3 font-semibold text-muted" aria-hidden="true">+91</span>
						<input
							id="login-mobile"
							ref="mobileField"
							v-model.trim="mobileInput"
							class="input min-h-12 text-lg tracking-wide"
							type="tel"
							inputmode="numeric"
							autocomplete="tel-national"
							maxlength="14"
							:placeholder="t('login.mobilePlaceholder')"
							:aria-invalid="fieldError ? 'true' : undefined"
							:aria-describedby="fieldError ? 'login-mobile-error login-mobile-hint' : 'login-mobile-hint'"
							required
						>
					</div>
					<p v-if="fieldError" id="login-mobile-error" class="field-error" role="alert">
						<CircleAlert :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />{{ fieldError }}
					</p>
					<p id="login-mobile-hint" class="field-hint">{{ t("login.mobileHint") }}</p>

					<fieldset class="mt-5">
						<legend class="field-label">{{ t("login.channelLabel") }}</legend>
						<div class="grid grid-cols-2 gap-2">
							<label
								v-for="option in (['sms', 'whatsapp'] as const)"
								:key="option"
								class="flex min-h-12 cursor-pointer items-center justify-center gap-2 rounded-xl border px-3 text-sm font-semibold transition-colors has-[:focus-visible]:outline-3 has-[:focus-visible]:outline-focus"
								:class="channel === option ? 'border-brand bg-brand-soft text-brand-ink' : 'border-line bg-surface text-ink'"
							>
								<input v-model="channel" class="sr-only" type="radio" name="channel" :value="option">
								<Smartphone v-if="option === 'sms'" :size="18" aria-hidden="true" />
								<MessageCircle v-else :size="18" aria-hidden="true" />
								{{ option === "sms" ? t("login.sms") : t("login.whatsapp") }}
							</label>
						</div>
					</fieldset>

					<p v-if="error" class="mt-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
						<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ error }}
					</p>

					<button type="submit" class="btn-primary btn-block mt-5 min-h-12" :disabled="busy">
						{{ busy ? t("login.sending") : t("login.sendCode") }}
						<ArrowRight v-if="!busy" :size="18" aria-hidden="true" />
					</button>
				</form>

				<!-- Step 2: code -->
				<form v-else-if="step === 'code'" novalidate @submit.prevent="submitCode()">
					<h2 class="text-lg font-semibold text-ink" tabindex="-1" data-step-title>{{ t("login.codeTitle") }}</h2>
					<p id="otp-help" class="mt-1 text-sm text-muted">
						{{ t("login.codeSent", { length: OTP_LENGTH, mobile: masked, channel: channelLabel }) }}
					</p>
					<p id="otp-label" class="sr-only">{{ t("login.codeLabel") }}</p>
					<div class="mt-5">
						<OtpInput
							ref="otpRef"
							v-model="code"
							:length="OTP_LENGTH"
							:disabled="busy"
							:invalid="Boolean(error)"
							labelled-by="otp-label"
							described-by="otp-help"
							autofocus
							@complete="submitCode()"
						/>
					</div>
					<p class="mt-2 text-xs text-muted">{{ t("login.expiresIn", { minutes: Math.max(1, Math.round(expiresIn / 60)) }) }}</p>

					<p v-if="error" class="mt-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
						<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ error }}
					</p>
					<p v-else-if="info" class="mt-4 rounded-xl bg-success-soft p-3 text-sm font-medium text-success" role="status">{{ info }}</p>

					<button type="submit" class="btn-primary btn-block mt-5 min-h-12" :disabled="busy || code.length !== OTP_LENGTH">
						{{ busy ? t("login.verifying") : t("login.verify") }}
					</button>

					<div class="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm">
						<button type="button" class="btn-ghost -ml-3 px-3" @click="changeNumber">{{ t("login.changeNumber") }}</button>
						<button
							type="button"
							class="btn-ghost -mr-3 px-3"
							:disabled="busy || resendIn > 0"
							@click="requestCode(true)"
						>
							<span v-if="resendIn > 0" aria-live="off">{{ t("login.resendIn", { seconds: resendIn }) }}</span>
							<span v-else>{{ t("login.resend") }}</span>
						</button>
					</div>
				</form>

				<!-- Step 3: student or parent -->
				<div v-else>
					<h2 class="text-lg font-semibold text-ink" tabindex="-1" data-step-title>{{ t("login.chooseTitle") }}</h2>
					<p class="mt-1 text-sm text-muted">{{ t("login.chooseBody") }}</p>
					<div class="mt-5 grid gap-3">
						<button
							v-for="option in choices"
							:key="option"
							type="button"
							class="btn-secondary btn-block min-h-14 justify-start text-left"
							:disabled="busy"
							@click="submitCode(option)"
						>
							{{ option === "student" ? t("login.asStudent") : t("login.asParent") }}
							<ArrowRight :size="18" class="ml-auto" aria-hidden="true" />
						</button>
					</div>
					<p v-if="error" class="mt-4 flex items-start gap-2 rounded-xl bg-danger-soft p-3 text-sm font-medium text-danger" role="alert">
						<CircleAlert :size="18" class="mt-0.5 shrink-0" aria-hidden="true" />{{ error }}
					</p>
					<button type="button" class="btn-ghost -ml-3 mt-4 px-3 text-sm" data-test="choose-change-number" @click="changeNumber">
						{{ t("login.changeNumber") }}
					</button>
				</div>
			</section>

			<p class="mt-4 flex items-start gap-2 px-1 text-xs text-muted">
				<ShieldCheck :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />
				<span>
					{{ t("login.privacyNote") }}
					<RouterLink :to="{ name: 'privacy' }">{{ t("login.privacyLink") }}</RouterLink>
				</span>
			</p>

			<div class="mt-6 grid gap-2">
				<RouterLink :to="{ name: 'help' }" class="btn-ghost justify-start">
					<LifeBuoy :size="18" aria-hidden="true" />{{ t("login.help") }}
				</RouterLink>
				<a href="/login" class="btn-ghost justify-start">{{ t("login.staff") }}</a>
			</div>

			<div class="mt-6 flex justify-center">
				<LanguageSwitch />
			</div>
		</main>
	</div>
</template>
