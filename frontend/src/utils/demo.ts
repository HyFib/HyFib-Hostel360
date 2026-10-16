/**
 * Demonstration sites: an open copy of the portal filled with fictitious data.
 *
 * The server says so in two places: the page's boot data (before sign-in, with the published
 * demo sign-ins for the sign-in page) and get_session's settings (after sign-in). Either one
 * is enough to show the banner. Nothing here can switch a site into demonstration mode; the
 * flag only changes what the portal shows.
 */
import type { PortalSettings } from "@/api/types";
import { t } from "@/i18n";
import { type DemoAccount, type DemoBoot, readBoot } from "./boot";

export const DEMO_INFO_URL = "/demo";

function isAccount(value: unknown): value is DemoAccount {
	if (!value || typeof value !== "object") return false;
	const row = value as Record<string, unknown>;
	return (
		(row.kind === "student" || row.kind === "parent") &&
		typeof row.name === "string" &&
		typeof row.mobile === "string" &&
		/^\d{10}$/.test(row.mobile)
	);
}

/** The demonstration details the page was rendered with, or null on an ordinary site. */
export function demoBoot(): Required<DemoBoot> | null {
	const demo = readBoot().demo;
	if (!demo || typeof demo !== "object") return null;
	const otp = typeof demo.otp === "string" && /^\d{4,8}$/.test(demo.otp) ? demo.otp : "";
	return {
		info_url: DEMO_INFO_URL,
		otp,
		accounts: Array.isArray(demo.accounts) ? demo.accounts.filter(isAccount) : [],
	};
}

export function isDemoSite(settings?: Pick<PortalSettings, "demo"> | null): boolean {
	return Boolean(settings?.demo) || demoBoot() !== null;
}

function joinNames(names: string[]): string {
	if (names.length < 2) return names.join("");
	return t("demo.joinNames", { first: names.slice(0, -1).join(", "), last: names[names.length - 1] });
}

const STORIES: Record<string, string> = {
	dues: "demo.stories.dues",
	leave: "demo.stories.leave",
	approve_leave: "demo.stories.approveLeave",
	two_wards: "demo.stories.twoWards",
};

/** "Student, Boys Hostel I · fees due" in the visitor's language. */
export function demoAccountLabel(account: DemoAccount): string {
	const role =
		account.kind === "parent"
			? account.wards?.length
				? t("demo.roles.parentOf", { wards: joinNames(account.wards) })
				: t("demo.roles.parent")
			: account.hostel
				? t("demo.roles.studentIn", { hostel: account.hostel })
				: t("demo.roles.student");
	const story = account.story ? STORIES[account.story] : undefined;
	return story ? `${role} · ${t(story)}` : role;
}
