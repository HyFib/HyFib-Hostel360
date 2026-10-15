import { createRouter, createWebHistory, type Router, type RouterHistory } from "vue-router";
import { t } from "@/i18n";
import { loadSession, session, useSession } from "@/stores/session";
import { showToast } from "@/stores/toast";
import { decideNavigation } from "./guards";
import { routes } from "./routes";

/** The portal lives at /hostel on the Frappe site. */
export const PORTAL_BASE = "/hostel/";

export function createAppRouter(history: RouterHistory = createWebHistory(PORTAL_BASE)): Router {
	const router = createRouter({
		history,
		routes,
		scrollBehavior(to, _from, saved) {
			if (saved) return saved;
			if (to.hash) return { el: to.hash };
			return { top: 0 };
		},
	});

	const { effectiveValidationStatus, studentStatus } = useSession();
	router.beforeEach(async (to) => {
		await loadSession();
		// Unreachable server: let the shell render its offline screen instead of guessing.
		if (session.status === "unreachable") return true;
		return decideNavigation(to, {
			loggedIn: session.loggedIn,
			kind: session.kind,
			consentRequired: session.consentRequired,
			profileConfirmed: session.profileConfirmed,
			validationStatus: effectiveValidationStatus.value,
			studentStatus: studentStatus.value,
			closedReason: session.closedReason,
		});
	});

	// Pages are loaded on demand. After a new release the old files are gone, and on a dead
	// connection they cannot be fetched; reload once into the new release, or explain.
	const CHUNK_ERROR =
		/dynamically imported module|Importing a module script failed|Loading chunk|Unable to preload CSS/i;
	router.onError((error, to) => {
		if (!CHUNK_ERROR.test(String((error as Error)?.message ?? error))) return;
		if (!navigator.onLine) {
			showToast(t("errors.network"), { tone: "error" });
			return;
		}
		const target = PORTAL_BASE.replace(/\/$/, "") + to.fullPath;
		let alreadyTried = false;
		try {
			alreadyTried = window.sessionStorage.getItem("h360.reloaded-for") === target;
			window.sessionStorage.setItem("h360.reloaded-for", target);
		} catch {
			// Without storage there is no loop guard; a single reload is still the best fix.
		}
		if (alreadyTried) showToast(t("errors.generic"), { tone: "error" });
		else window.location.assign(target);
	});

	router.afterEach((to) => {
		try {
			window.sessionStorage.removeItem("h360.reloaded-for");
		} catch {
			// Storage unavailable: nothing to clear.
		}
		const name = session.settings.institution_short_name;
		const page = to.meta.title ? t(to.meta.title) : "";
		document.title = [page, name || "Hostel360"].filter(Boolean).join(" · ");
	});

	return router;
}
