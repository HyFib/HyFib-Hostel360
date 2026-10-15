/**
 * Where a visitor may go, as a pure function of the route and the session.
 *
 * Order matters: sign-in first, then the privacy notice (consent must come before any
 * personal data is shown), then first-run profile confirmation for students, then role and
 * verification checks.
 */
import type { RouteLocationNormalized, RouteLocationRaw } from "vue-router";
import type { PortalKind } from "@/api/types";

declare module "vue-router" {
	interface RouteMeta {
		/** Reachable without signing in. */
		public?: boolean;
		/** Only for visitors who are not signed in (the sign-in page). */
		guestOnly?: boolean;
		/** Only for this kind of portal user. */
		kind?: PortalKind;
		/** Needs a registration the hostel office has verified. */
		requiresValidated?: boolean;
		/** Allowed while the privacy notice or profile confirmation is still pending. */
		firstRun?: boolean;
		/** Page title key. */
		title?: string;
		/** Render without the app header and navigation. */
		bare?: boolean;
	}
}

export interface GuardState {
	loggedIn: boolean;
	kind: PortalKind | null;
	consentRequired: boolean;
	profileConfirmed: boolean;
	validationStatus: string;
}

/** Only same-app paths are accepted as a post-login destination (no open redirects). */
export function safeRedirect(value: unknown): string | null {
	if (typeof value !== "string") return null;
	if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return null;
	if (value.startsWith("/login")) return null;
	return value;
}

function redirectQuery(to: RouteLocationNormalized): Record<string, string> {
	return to.fullPath && to.fullPath !== "/" ? { redirect: to.fullPath } : {};
}

export function decideNavigation(to: RouteLocationNormalized, state: GuardState): true | RouteLocationRaw {
	const meta = to.meta;

	if (!state.loggedIn) {
		return meta.public ? true : { name: "login", query: redirectQuery(to) };
	}

	if (meta.guestOnly) {
		const target = safeRedirect(to.query.redirect);
		return target ?? { name: "home" };
	}

	if (state.consentRequired && to.name !== "privacy" && !meta.public) {
		return { name: "privacy", query: redirectQuery(to) };
	}

	if (state.kind === "student" && !state.profileConfirmed && !meta.firstRun && !meta.public) {
		return { name: "confirm-details", query: redirectQuery(to) };
	}

	if (meta.kind && meta.kind !== state.kind) {
		return { name: "home" };
	}

	if (meta.requiresValidated && state.validationStatus !== "Validated") {
		return { name: "home", query: { notice: "verification" } };
	}

	return true;
}
