import { afterEach } from "vitest";
import { locale } from "@/i18n";

// jsdom has no layout engine; the router's scroll restoration only needs the call to exist.
window.scrollTo = () => undefined;

afterEach(() => {
	locale.value = "en";
	document.body.innerHTML = "";
	try {
		window.localStorage.clear();
	} catch {
		// jsdom always has storage; kept for parity with the app's own guard.
	}
});
