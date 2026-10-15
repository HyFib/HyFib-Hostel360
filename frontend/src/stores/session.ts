/**
 * Who is signed in, and which ward a parent is looking at.
 *
 * Loaded once at start-up from get_session; the router guards read it to decide where a
 * visitor may go. Nothing here is persisted except the parent's last chosen ward.
 */
import { computed, reactive, readonly } from "vue";
import { logout as apiLogout } from "@/api/auth";
import { ApiError, initCsrf, refreshCsrfToken, setCsrfToken } from "@/api/client";
import { getSession } from "@/api/portal";
import type { PortalKind, PortalSession, PortalSettings, Ward } from "@/api/types";
import { readBoot, updateBoot } from "@/utils/boot";
import { getItem, removeItem, setItem } from "@/utils/storage";

export type SessionStatus = "idle" | "loading" | "ready" | "unreachable";

export interface SessionState {
	status: SessionStatus;
	loggedIn: boolean;
	user: string | null;
	kind: PortalKind | null;
	displayName: string;
	mobileMasked: string;
	wards: Ward[];
	activeStudent: string | null;
	validationStatus: string;
	profileConfirmed: boolean;
	consentRequired: boolean;
	privacyNoticeVersion: string;
	privacyNotice: string | null;
	settings: PortalSettings;
}

function emptyState(): SessionState {
	return {
		status: "idle",
		loggedIn: false,
		user: null,
		kind: null,
		displayName: "",
		mobileMasked: "",
		wards: [],
		activeStudent: null,
		validationStatus: "",
		profileConfirmed: false,
		consentRequired: false,
		privacyNoticeVersion: "",
		privacyNotice: null,
		settings: {},
	};
}

const state = reactive<SessionState>(emptyState());
let pending: Promise<void> | null = null;

const wardKey = (user: string) => `ward.${user}`;

function settingsFromBoot(): PortalSettings {
	const boot = readBoot();
	return {
		institution_name: boot.institution_name,
		institution_short_name: boot.institution_short_name,
		logo: boot.logo,
		support_phone: boot.support_phone,
		support_email: boot.support_email,
	};
}

function pickWard(user: string, wards: Ward[]): string | null {
	if (!wards.length) return null;
	const saved = getItem(wardKey(user));
	return wards.some((ward) => ward.student === saved) ? saved : wards[0].student;
}

/** Copy a get_session answer into the store (also used by tests). */
export function applySession(data: PortalSession | null): void {
	const base = emptyState();
	if (!data || !data.user || data.user === "Guest") {
		Object.assign(state, base, { status: "ready", settings: { ...settingsFromBoot() } });
		return;
	}
	const wards = Array.isArray(data.wards) ? data.wards : [];
	Object.assign(state, base, {
		status: "ready",
		loggedIn: true,
		user: data.user,
		kind: data.kind,
		displayName: data.display_name || "",
		mobileMasked: data.mobile_masked || "",
		wards,
		activeStudent: data.kind === "parent" ? pickWard(data.user, wards) : (wards[0]?.student ?? null),
		validationStatus: data.validation_status || "",
		profileConfirmed: Boolean(data.profile_confirmed),
		consentRequired: Boolean(data.consent_required),
		privacyNoticeVersion: data.privacy_notice_version || "",
		privacyNotice: data.privacy_notice ?? data.settings?.privacy_notice ?? null,
		settings: { ...settingsFromBoot(), ...(data.settings || {}) },
	});
}

export function resetSession(): void {
	Object.assign(state, emptyState());
	pending = null;
}

async function fetchSession(): Promise<void> {
	state.status = "loading";
	try {
		applySession(await getSession());
	} catch (error) {
		if (error instanceof ApiError && error.isNetwork) {
			state.status = "unreachable";
			return;
		}
		// 403 / 401: not signed in (or the session ended). Any other failure is treated the
		// same way so the visitor can at least reach the sign-in page.
		applySession(null);
	}
}

/** Load the session once; later calls wait for the same request. */
export function loadSession(force = false): Promise<void> {
	if (force || !pending || state.status === "unreachable") {
		const boot = readBoot();
		pending =
			boot.is_logged_in === false && !force
				? Promise.resolve(applySession(null))
				: fetchSession();
	}
	return pending;
}

/** After a successful OTP sign-in: new session cookie, so a new CSRF token and session data. */
export async function completeSignIn(): Promise<void> {
	try {
		await refreshCsrfToken();
	} catch {
		// The next write retries with a fresh token anyway.
	}
	updateBoot({ is_logged_in: true });
	await loadSession(true);
}

export async function signOut(): Promise<void> {
	try {
		await apiLogout();
	} catch {
		// Even if the server cannot be reached, forget the session on this device.
	}
	if (state.user) removeItem(wardKey(state.user));
	setCsrfToken("");
	updateBoot({ is_logged_in: false, kind: null });
	applySession(null);
	pending = Promise.resolve();
}

export function setActiveStudent(student: string): void {
	if (!state.wards.some((ward) => ward.student === student)) return;
	state.activeStudent = student;
	if (state.user) setItem(wardKey(state.user), student);
}

export function markConsentAccepted(version: string): void {
	state.consentRequired = false;
	state.privacyNoticeVersion = version || state.privacyNoticeVersion;
}

export function markProfileConfirmed(): void {
	state.profileConfirmed = true;
}

export function initSessionFromPage(): void {
	initCsrf();
}

const activeWard = computed<Ward | null>(
	() => state.wards.find((ward) => ward.student === state.activeStudent) ?? state.wards[0] ?? null,
);
const isParent = computed(() => state.kind === "parent");
const isStudent = computed(() => state.kind === "student");
const isValidated = computed(() => state.validationStatus === "Validated");
const firstName = computed(() => (state.displayName || "").trim().split(/\s+/)[0] || "");
const institutionName = computed(
	() => state.settings.institution_short_name || state.settings.institution_name || readBoot().institution_short_name || "",
);
const paymentEnabled = computed(() => state.settings.payment_enabled === undefined || Boolean(state.settings.payment_enabled));
const reductionMinDays = computed(() => Number(state.settings.mess_reduction_min_days) || 3);

/** `student` argument for portal calls: the chosen ward for parents, nothing for students. */
export function studentArg(): string | undefined {
	return state.kind === "parent" ? (state.activeStudent ?? undefined) : undefined;
}

export const session = readonly(state);

export function useSession() {
	return {
		session,
		activeWard,
		isParent,
		isStudent,
		isValidated,
		firstName,
		institutionName,
		paymentEnabled,
		reductionMinDays,
		studentArg,
		setActiveStudent,
		signOut,
	};
}
