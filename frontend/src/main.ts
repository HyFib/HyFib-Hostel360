import { createApp, watch } from "vue";
import App from "./App.vue";
import { configureApi } from "./api/client";
import { initI18n, locale, t } from "./i18n";
import { createAppRouter } from "./router";
import { resetInbox } from "./stores/inbox";
import { reportServerReachable, watchConnection } from "./stores/network";
import {
	closeAccess,
	initSessionFromPage,
	loadSession,
	markConsentRequired,
	onLocaleChanged,
	resetToGuest,
	session,
} from "./stores/session";
import { showToast } from "./stores/toast";
import { readBoot } from "./utils/boot";
import "./styles/main.css";

const boot = readBoot();
const languageReady = initI18n(boot.lang);
initSessionFromPage();
watchConnection();

const router = createAppRouter();
let verifyingSession = false;

/** Back to the sign-in page, coming back here afterwards unless this page is public anyway. */
async function toSignIn(): Promise<void> {
	const current = router.currentRoute.value;
	if (current.name === "login") return;
	await router.replace({ name: "login", query: current.meta.public ? {} : { redirect: current.fullPath } });
}

/** The session lapsed: forget it here, say so once, and go to sign-in. */
async function endSession(): Promise<void> {
	if (!session.loggedIn) return;
	resetToGuest();
	resetInbox();
	showToast(t("errors.sessionExpired"), { tone: "info" });
	await toSignIn();
}

configureApi({
	onNetworkStatus: reportServerReachable,
	onSessionEnded: endSession,
	async onForbidden() {
		// A 403 while signed in may mean the session ended (sign-out in another tab). Re-check;
		// the check can itself answer 403, so only one runs at a time.
		if (!session.loggedIn || verifyingSession) return false;
		verifyingSession = true;
		try {
			await loadSession(true);
		} finally {
			verifyingSession = false;
		}
		if (session.loggedIn) return false;
		resetInbox();
		showToast(t("errors.sessionExpired"), { tone: "info" });
		await toSignIn();
		return true;
	},
	async onConsentRequired() {
		// A new privacy notice was published mid-session: it is read and accepted before anything else.
		if (!session.loggedIn) return;
		markConsentRequired();
		const current = router.currentRoute.value;
		if (current.name !== "privacy") await router.replace({ name: "privacy", query: { redirect: current.fullPath } });
	},
	async onAccessClosed(error) {
		// The record may no longer use the portal: the sign-in page shows the server's reason.
		const wasSignedIn = session.loggedIn;
		await closeAccess(error.message);
		resetInbox();
		if (wasSignedIn) await router.replace({ name: "login" });
	},
});

// Server messages and notifications follow the language chosen on screen.
watch(locale, () => onLocaleChanged());

const app = createApp(App);
app.use(router);
// Wait for the Tamil catalogue (when chosen) so the first screen isn't shown in English first.
void languageReady.finally(() => app.mount("#app"));

if (import.meta.env.PROD) {
	void import("./pwa").then(({ registerServiceWorker }) => registerServiceWorker());
}
