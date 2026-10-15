/**
 * Who is signed in, and which ward a parent is looking at.
 *
 * Loaded once at start-up from get_session; the router guards read it to decide where a
 * visitor may go. Nothing here is persisted except the parent's last chosen ward.
 */
import { computed, reactive, readonly } from "vue";
import { logout as apiLogout } from "@/api/auth";
import { ACCESS_CLOSED, ApiError, initCsrf, refreshCsrfToken, setCsrfToken } from "@/api/client";
import { getSession, setLanguage } from "@/api/portal";
import type { PortalKind, PortalSession, PortalSettings, Ward } from "@/api/types";
import { locale } from "@/i18n";
import { readBoot, updateBoot } from "@/utils/boot";
import { SITE_TIME_ZONE } from "@/utils/format";
import { getItem, removeItem, setItem } from "@/utils/storage";

/** Hostel Student status that opens every feature; others keep only dues and the basics. */
export const ACTIVE_STATUS = "Active";

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
	/** Language the server writes this user's messages in. */
	language: string | null;
	/** Why the last session was closed by the server (record not validated or closed), for the sign-in page. */
	closedReason: string;
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
		language: null,
		closedReason: "",
		settings: {},
	};
}

const state = reactive<SessionState>(emptyState());
let pending: Promise<void> | null = null;
let closing: Promise<void> | null = null;

const wardKey = (user: string) => `ward.${user}`;

function settingsFromBoot(): PortalSettings {
	const boot = readBoot();
	return {
		institution_name: boot.institution_name,
		institution_short_name: boot.institution_short_name,
		logo: boot.logo,
		office_phone: boot.office_phone ?? undefined,
		office_email: boot.office_email ?? undefined,
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
		// A reason the server gave for closing the session stays until the next sign-in.
		Object.assign(state, base, { status: "ready", closedReason: state.closedReason, settings: { ...settingsFromBoot() } });
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
		language: data.language || null,
		settings: { ...settingsFromBoot(), ...(data.settings || {}) },
	});
}

/** Forget the session on this device (the server already ended it, or refused it). */
export function resetToGuest(): void {
	setCsrfToken("");
	updateBoot({ is_logged_in: false, kind: null });
	applySession(null);
	pending = Promise.resolve();
}

/**
 * The server refused this session because the record may no longer use the portal (TR 3.1,
 * 4.1): end it here and on the server, and keep the reason for the sign-in page.
 */
export function closeAccess(reason: string): Promise<void> {
	state.closedReason = reason;
	// The refusal can reach the app from the session check and from a page's own call at once.
	closing ??= (async () => {
		try {
			await apiLogout();
		} catch {
			// The cookie is useless anyway; the server refuses it on every call.
		}
		resetToGuest();
	})().finally(() => {
		closing = null;
	});
	return closing;
}

export function clearClosedReason(): void {
	state.closedReason = "";
}

/** Server messages follow the language on screen; align the user's saved language once. */
function syncServerLanguage(serverLanguage: string | null | undefined): void {
	const wanted = locale.value;
	if (!state.loggedIn || (serverLanguage || "en") === wanted) return;
	void setLanguage(wanted)
		.then((result) => {
			state.language = result?.language || wanted;
		})
		.catch(() => undefined);
}

/** The language switch was used: keep the server in step for this signed-in user. */
export function onLocaleChanged(): void {
	syncServerLanguage(state.language);
}

export function resetSession(): void {
	Object.assign(state, emptyState());
	pending = null;
}

async function fetchSession(): Promise<void> {
	state.status = "loading";
	try {
		const data = await getSession();
		applySession(data);
		syncServerLanguage(data?.language);
	} catch (error) {
		if (error instanceof ApiError && error.isNetwork) {
			state.status = "unreachable";
			return;
		}
		if (error instanceof ApiError && error.excType === ACCESS_CLOSED) {
			await closeAccess(error.message);
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
	clearClosedReason();
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
	state.closedReason = "";
	resetToGuest();
}

export function setActiveStudent(student: string): void {
	if (!state.wards.some((ward) => ward.student === student)) return;
	state.activeStudent = student;
	if (state.user) setItem(wardKey(state.user), student);
}

/** The server asked for the (new) privacy notice to be accepted before anything else. */
export function markConsentRequired(): void {
	if (state.loggedIn) state.consentRequired = true;
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
/**
 * Validation of the student being viewed. A guardian counts as validated once any one ward
 * is, so for a parent the selected ward's own status decides what is open.
 */
const effectiveValidationStatus = computed(() =>
	state.kind === "parent" ? (activeWard.value?.validation_status ?? state.validationStatus) : state.validationStatus,
);
/** Hostel Student status of the student being viewed (Active, Applied, Suspended…). */
const studentStatus = computed(() => activeWard.value?.status ?? "");
const isValidated = computed(() => effectiveValidationStatus.value === "Validated");
/** Every feature is open: validated and Active (an unknown status from an older server counts as Active). */
const hasFullAccess = computed(() => isValidated.value && (!studentStatus.value || studentStatus.value === ACTIVE_STATUS));
const firstName = computed(() => (state.displayName || "").trim().split(/\s+/)[0] || "");
const institutionName = computed(
	() => state.settings.institution_short_name || state.settings.institution_name || readBoot().institution_short_name || "",
);
const paymentEnabled = computed(() => state.settings.payment_enabled === undefined || Boolean(state.settings.payment_enabled));
const reductionMinDays = computed(() => Number(state.settings.mess_reduction_min_days) || 3);
const messDayCutoffHour = computed(() => {
	const hour = Number(state.settings.mess_day_cutoff_hour);
	return Number.isInteger(hour) && hour >= 0 && hour <= 23 ? hour : 12;
});
/** The hostel's time zone: leave and mess dates are wall-clock times there. */
const siteTimeZone = computed(() => state.settings.time_zone || SITE_TIME_ZONE);

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
		hasFullAccess,
		effectiveValidationStatus,
		studentStatus,
		firstName,
		institutionName,
		paymentEnabled,
		reductionMinDays,
		messDayCutoffHour,
		siteTimeZone,
		studentArg,
		setActiveStudent,
		signOut,
	};
}
