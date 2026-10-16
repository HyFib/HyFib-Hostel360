/**
 * Service worker registration.
 *
 * The worker file is served from /assets/hostel360/frontend/sw.js and controls /hostel,
 * which needs the web server to send "Service-Worker-Allowed: /hostel" with that file. If it
 * does not, registration fails quietly and the portal simply works without offline support.
 */
import { registerSW } from "virtual:pwa-register";
import { t } from "@/i18n";
import { showToast } from "@/stores/toast";

const UPDATE_CHECK_MS = 60 * 60 * 1000;

export function registerServiceWorker(): void {
	if (!("serviceWorker" in navigator)) return;
	const update = registerSW({
		onNeedRefresh() {
			showToast(t("network.update"), {
				tone: "info",
				timeout: 0,
				actionLabel: t("network.updateAction"),
				action: () => void update(true),
			});
		},
		onRegisteredSW(_url, registration) {
			if (!registration) return;
			// Long-lived tabs (an installed app left open) still pick up new releases.
			window.setInterval(() => {
				if (document.visibilityState === "visible" && navigator.onLine) void registration.update();
			}, UPDATE_CHECK_MS);
		},
		onRegisterError() {
			// Offline support is optional; nothing for the user to do.
		},
	});
}
