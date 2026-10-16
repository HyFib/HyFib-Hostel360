/**
 * Connection state shown by the offline banner.
 *
 * navigator.onLine only knows whether a network interface is up; a phone on a captive
 * Wi-Fi or a dead mobile connection still reports "online". Real request failures from the
 * API client are therefore combined with the browser's events.
 */
import { computed, ref } from "vue";

const browserOnline = ref(typeof navigator === "undefined" ? true : navigator.onLine !== false);
const serverReachable = ref(true);
let listening = false;

export const isOffline = computed(() => !browserOnline.value || !serverReachable.value);

export function reportServerReachable(reachable: boolean): void {
	serverReachable.value = reachable;
}

export function watchConnection(): void {
	if (listening || typeof window === "undefined") return;
	listening = true;
	window.addEventListener("online", () => {
		browserOnline.value = true;
		serverReachable.value = true;
	});
	window.addEventListener("offline", () => {
		browserOnline.value = false;
	});
}

export function useNetwork() {
	return { isOffline };
}
