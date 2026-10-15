import { createApp } from "vue";
import App from "./App.vue";
import { configureApi } from "./api/client";
import { initI18n, t } from "./i18n";
import { createAppRouter } from "./router";
import { reportServerReachable, watchConnection } from "./stores/network";
import { initSessionFromPage, loadSession, session } from "./stores/session";
import { showToast } from "./stores/toast";
import { readBoot } from "./utils/boot";
import "./styles/main.css";

const boot = readBoot();
const languageReady = initI18n(boot.lang);
initSessionFromPage();
watchConnection();

const router = createAppRouter();
let verifyingSession = false;

configureApi({
	onNetworkStatus: reportServerReachable,
	async onForbidden() {
		// A 403 while signed in usually means the session ended (timeout, sign-out elsewhere).
		// Re-checking the session can itself answer 403, so only one check runs at a time.
		if (!session.loggedIn || verifyingSession) return;
		verifyingSession = true;
		try {
			await loadSession(true);
		} finally {
			verifyingSession = false;
		}
		if (!session.loggedIn) {
			showToast(t("errors.sessionExpired"), { tone: "info" });
			const current = router.currentRoute.value;
			await router.replace({ name: "login", query: current.meta.public ? {} : { redirect: current.fullPath } });
		}
	},
});

const app = createApp(App);
app.use(router);
// Wait for the Tamil catalogue (when chosen) so the first screen isn't shown in English first.
void languageReady.finally(() => app.mount("#app"));

if (import.meta.env.PROD) {
	void import("./pwa").then(({ registerServiceWorker }) => registerServiceWorker());
}
