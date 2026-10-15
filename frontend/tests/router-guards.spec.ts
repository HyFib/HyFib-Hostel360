import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, type RouteLocationNormalized } from "vue-router";
import { ApiError } from "@/api/client";
import type { PortalSession } from "@/api/types";
import { decideNavigation, safeRedirect, type GuardState } from "@/router/guards";
import { routes } from "@/router/routes";

vi.mock("@/api/portal", () => ({ getSession: vi.fn() }));
vi.mock("@/api/auth", () => ({ logout: vi.fn(() => Promise.resolve({})) }));

const portal = await import("@/api/portal");
const { createAppRouter } = await import("@/router");
const { resetSession } = await import("@/stores/session");
const { resetBoot } = await import("@/utils/boot");

const guest: GuardState = {
	loggedIn: false,
	kind: null,
	consentRequired: false,
	profileConfirmed: false,
	validationStatus: "",
};
const student: GuardState = {
	loggedIn: true,
	kind: "student",
	consentRequired: false,
	profileConfirmed: true,
	validationStatus: "Validated",
};
const parent: GuardState = { ...student, kind: "parent", profileConfirmed: false };

function route(name: string, fullPath: string, query: Record<string, string> = {}): RouteLocationNormalized {
	const record = routes.find((item) => item.name === name);
	return {
		name,
		fullPath,
		path: fullPath.split("?")[0],
		query,
		params: {},
		hash: "",
		matched: [],
		meta: record?.meta ?? {},
		redirectedFrom: undefined,
	};
}

describe("decideNavigation", () => {
	it("sends guests to sign-in and remembers where they were going", () => {
		expect(decideNavigation(route("fees", "/fees"), guest)).toEqual({ name: "login", query: { redirect: "/fees" } });
		expect(decideNavigation(route("home", "/"), guest)).toEqual({ name: "login", query: {} });
	});

	it("lets guests open public pages", () => {
		expect(decideNavigation(route("login", "/login"), guest)).toBe(true);
		expect(decideNavigation(route("help", "/help"), guest)).toBe(true);
		expect(decideNavigation(route("privacy", "/privacy"), guest)).toBe(true);
	});

	it("keeps signed-in users away from the sign-in page", () => {
		expect(decideNavigation(route("login", "/login"), student)).toEqual({ name: "home" });
		expect(decideNavigation(route("login", "/login?redirect=/fees", { redirect: "/fees" }), student)).toBe("/fees");
	});

	it("refuses redirects that leave the portal", () => {
		expect(safeRedirect("//evil.example/phish")).toBeNull();
		expect(safeRedirect("https://evil.example")).toBeNull();
		expect(safeRedirect("/\\evil.example")).toBeNull();
		expect(decideNavigation(route("login", "/login", { redirect: "//evil.example" }), student)).toEqual({
			name: "home",
		});
	});

	it("asks for privacy consent before anything else", () => {
		const state = { ...student, consentRequired: true, profileConfirmed: false };
		expect(decideNavigation(route("fees", "/fees"), state)).toEqual({ name: "privacy", query: { redirect: "/fees" } });
		expect(decideNavigation(route("confirm-details", "/confirm-details"), state)).toMatchObject({ name: "privacy" });
		expect(decideNavigation(route("privacy", "/privacy"), state)).toBe(true);
		expect(decideNavigation(route("help", "/help"), state)).toBe(true);
	});

	it("asks unconfirmed students to confirm their details", () => {
		const state = { ...student, profileConfirmed: false };
		expect(decideNavigation(route("home", "/"), state)).toEqual({ name: "confirm-details", query: {} });
		expect(decideNavigation(route("leave", "/leave"), state)).toEqual({
			name: "confirm-details",
			query: { redirect: "/leave" },
		});
		expect(decideNavigation(route("confirm-details", "/confirm-details"), state)).toBe(true);
	});

	it("does not ask parents to confirm student details", () => {
		expect(decideNavigation(route("home", "/"), parent)).toBe(true);
		expect(decideNavigation(route("confirm-details", "/confirm-details"), parent)).toEqual({ name: "home" });
	});

	it("keeps each role to its own pages", () => {
		expect(decideNavigation(route("leave-approvals", "/leave/approvals"), student)).toEqual({ name: "home" });
		expect(decideNavigation(route("leave-approvals", "/leave/approvals"), parent)).toBe(true);
		expect(decideNavigation(route("feedback", "/feedback"), parent)).toEqual({ name: "home" });
	});

	it("explains a session the server closed before offering sign-in again", () => {
		const closed = { ...guest, closedReason: "Portal access for this hostel record has been closed." };
		expect(decideNavigation(route("fees", "/fees"), closed)).toEqual({ name: "access-closed" });
		expect(decideNavigation(route("home", "/"), closed)).toEqual({ name: "access-closed" });
		expect(decideNavigation(route("access-closed", "/access-closed"), closed)).toBe(true);
		// Public pages stay open, so the person can read help or sign in again.
		expect(decideNavigation(route("login", "/login"), closed)).toBe(true);
		expect(decideNavigation(route("help", "/help"), closed)).toBe(true);
		// Without a reason there is nothing to explain.
		expect(decideNavigation(route("access-closed", "/access-closed"), guest)).toEqual({ name: "login" });
		expect(decideNavigation(route("access-closed", "/access-closed"), student)).toEqual({ name: "home" });
	});

	it("holds back full features until the registration is verified", () => {
		const pending = { ...student, validationStatus: "Pending" };
		expect(decideNavigation(route("fees", "/fees"), pending)).toEqual({
			name: "home",
			query: { notice: "verification" },
		});
		expect(decideNavigation(route("profile", "/profile"), pending)).toBe(true);
		expect(decideNavigation(route("fees", "/fees"), student)).toBe(true);
	});
});

describe("router", () => {
	const session = (overrides: Partial<PortalSession> = {}): PortalSession => ({
		user: "student@portal.hostel360.invalid",
		kind: "student",
		display_name: "Priya Raman",
		wards: [{ student: "REG001", full_name: "Priya Raman" }],
		validation_status: "Validated",
		profile_confirmed: 1,
		consent_required: 0,
		privacy_notice_version: "1.0",
		settings: {},
		...overrides,
	});

	beforeEach(() => {
		resetSession();
		resetBoot();
		vi.mocked(portal.getSession).mockReset();
	});

	it("redirects a guest to /hostel/login", async () => {
		vi.mocked(portal.getSession).mockRejectedValue(new ApiError("permission", "Not permitted", { status: 403 }));
		const router = createAppRouter(createMemoryHistory("/hostel/"));
		await router.push("/fees");
		expect(router.currentRoute.value.name).toBe("login");
		expect(router.currentRoute.value.query.redirect).toBe("/fees");
		expect(router.resolve({ name: "login" }).href).toBe("/hostel/login");
	});

	it("routes a first-time student through consent", async () => {
		vi.mocked(portal.getSession).mockResolvedValue(session({ consent_required: 1, profile_confirmed: 0 }));
		const router = createAppRouter(createMemoryHistory("/hostel/"));
		await router.push("/grievances");
		expect(router.currentRoute.value.name).toBe("privacy");
		expect(router.currentRoute.value.query.redirect).toBe("/grievances");
	});

	it("shows why the server closed a session instead of an empty sign-in form", async () => {
		vi.mocked(portal.getSession).mockRejectedValue(
			new ApiError("permission", "Your hostel registration has not been validated by the hostel office yet.", {
				status: 403,
				excType: "PortalAccessClosed",
			}),
		);
		const router = createAppRouter(createMemoryHistory("/hostel/"));
		await router.push("/fees");
		expect(router.currentRoute.value.name).toBe("access-closed");
		await router.push("/login");
		expect(router.currentRoute.value.name).toBe("login");
	});

	it("lets a confirmed, verified student straight in", async () => {
		vi.mocked(portal.getSession).mockResolvedValue(session());
		const router = createAppRouter(createMemoryHistory("/hostel/"));
		await router.push("/grievances");
		expect(router.currentRoute.value.name).toBe("grievances");
		expect(portal.getSession).toHaveBeenCalledTimes(1);
		await router.push("/leave");
		expect(portal.getSession).toHaveBeenCalledTimes(1);
	});
});
