/** Demonstration sites: the banner, the sign-in helper and the demo code on the login page. */
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiError } from "@/api/client";
import { locale, setLocale } from "@/i18n";
import { applySession, resetSession } from "@/stores/session";
import { resetBoot } from "@/utils/boot";
import { demoAccountLabel, demoBoot, isDemoSite } from "@/utils/demo";

const SENT = { sent: true, masked: "90xxxxxx01", resend_after: 60, expires_in: 300 };
const DEMO = {
	info_url: "/demo",
	otp: "246810",
	accounts: [
		{ kind: "student", name: "Ragul Velu", mobile: "9000000001", hostel: "Boys Hostel I", story: "dues" },
		{ kind: "parent", name: "Velu", mobile: "9000000012", story: "two_wards", wards: ["Ragul", "Harini"] },
	],
};

function setBoot(values: Record<string, unknown>) {
	document.body.innerHTML = `<script type="application/json" id="h360-boot">${JSON.stringify(values)}</script>`;
	resetBoot();
}

type ErrorClass = typeof ApiError;

/** ``refuse``: make sendOtp fail with the ApiError class the freshly loaded page uses. */
async function mountLogin(refuse?: (error: ErrorClass) => Error) {
	vi.resetModules();
	const client = await import("@/api/client");
	const sendOtp = vi.fn(() => (refuse ? Promise.reject(refuse(client.ApiError)) : Promise.resolve(SENT)));
	vi.doMock("@/api/auth", () => ({ sendOtp, verifyOtp: vi.fn(), logout: vi.fn() }));
	const { createMemoryHistory, createRouter } = await import("vue-router");
	const { routes } = await import("@/router/routes");
	const LoginView = (await import("@/views/LoginView.vue")).default;
	const router = createRouter({ history: createMemoryHistory("/hostel/"), routes });
	await router.push("/login");
	await router.isReady();
	const wrapper = mount(LoginView, { global: { plugins: [router] }, attachTo: document.body });
	return { wrapper, sendOtp };
}

describe("demonstration boot data", () => {
	afterEach(() => resetBoot());

	it("is absent on an ordinary site", () => {
		setBoot({ otp_email: false });
		expect(demoBoot()).toBeNull();
		expect(isDemoSite({})).toBe(false);
	});

	it("keeps only well-formed accounts and codes", () => {
		setBoot({
			demo: {
				otp: "12ab",
				accounts: [...DEMO.accounts, { kind: "staff", name: "Admin", mobile: "9000000700" }, { name: "x" }],
			},
		});
		const demo = demoBoot();
		expect(demo?.otp).toBe("");
		expect(demo?.accounts.map((a) => a.mobile)).toEqual(["9000000001", "9000000012"]);
		expect(isDemoSite(null)).toBe(true);
	});

	it("is also recognised from the signed-in session's settings", () => {
		setBoot({});
		expect(isDemoSite({ demo: true })).toBe(true);
	});

	it("words each account's role in the visitor's language", async () => {
		expect(demoAccountLabel(DEMO.accounts[0] as never)).toBe("Student, Boys Hostel I · fees due");
		expect(demoAccountLabel(DEMO.accounts[1] as never)).toBe("Parent of Ragul and Harini · two wards");
		await setLocale("ta");
		expect(demoAccountLabel(DEMO.accounts[1] as never)).toBe(
			"Ragul மற்றும் Harini அவர்களின் பெற்றோர் · இரண்டு பிள்ளைகள்",
		);
		locale.value = "en";
	});
});

describe("DemoBanner", () => {
	beforeEach(() => resetSession());
	afterEach(() => resetBoot());

	it("shows nothing on an ordinary site", async () => {
		setBoot({});
		const DemoBanner = (await import("@/components/DemoBanner.vue")).default;
		const wrapper = mount(DemoBanner);
		expect(wrapper.find('[data-test="demo-banner"]').exists()).toBe(false);
	});

	it("cannot be dismissed and links to the demo page", async () => {
		setBoot({ demo: DEMO });
		const DemoBanner = (await import("@/components/DemoBanner.vue")).default;
		const wrapper = mount(DemoBanner);
		const banner = wrapper.find('[data-test="demo-banner"]');
		expect(banner.text()).toContain("Demonstration site — all data is fictitious");
		expect(banner.find("a").attributes("href")).toBe("/demo");
		expect(banner.find("button").exists()).toBe(false);
	});

	it("follows the session after sign-in", async () => {
		setBoot({});
		applySession({ user: "student@x", kind: "student", display_name: "S", wards: [], settings: { demo: true } });
		const DemoBanner = (await import("@/components/DemoBanner.vue")).default;
		const wrapper = mount(DemoBanner);
		expect(wrapper.find('[data-test="demo-banner"]').exists()).toBe(true);
	});
});

describe("LoginView on a demonstration site", () => {
	beforeEach(() => resetSession());
	afterEach(() => {
		vi.doUnmock("@/api/auth");
		resetBoot();
	});

	it("has no demo helper on an ordinary site", async () => {
		setBoot({ otp_email: false });
		const { wrapper } = await mountLogin();
		expect(wrapper.find('[data-test="demo-sign-in"]').exists()).toBe(false);
		wrapper.unmount();
	});

	it("lists the demo accounts with the code and signs in with one tap", async () => {
		setBoot({ demo: DEMO });
		const { wrapper, sendOtp } = await mountLogin();
		const helper = wrapper.find('[data-test="demo-sign-in"]');
		expect(helper.text()).toContain("Ragul Velu");
		expect(helper.text()).toContain("Parent of Ragul and Harini");
		expect(helper.find('[data-test="demo-code"]').text()).toBe("246810");

		await helper.find('[data-test="demo-account-9000000001"]').trigger("click");
		await flushPromises();
		expect(sendOtp).toHaveBeenCalledWith("9000000001", "sms");
		expect(wrapper.find('[data-test="demo-code-hint"]').text()).toContain("246810");
		expect(wrapper.find('[data-test="demo-sign-in"]').exists()).toBe(false);
		wrapper.unmount();
	});

	it("goes on to the code step when the account's code was just requested by someone else", async () => {
		setBoot({ demo: DEMO });
		const { wrapper } = await mountLogin(
			(ClientError) => new ClientError("rate_limit", "Please wait 42 seconds before requesting another code."),
		);
		await wrapper.find('[data-test="demo-account-9000000012"]').trigger("click");
		await flushPromises();
		expect(wrapper.find("#otp-help").exists()).toBe(true);
		expect(wrapper.text()).toContain("This demo account was used a moment ago");
		expect(wrapper.find('[data-test="demo-code-hint"]').exists()).toBe(true);
		wrapper.unmount();
	});

	it("keeps an ordinary site's rate limit message", async () => {
		setBoot({});
		const { wrapper } = await mountLogin(
			(ClientError) => new ClientError("rate_limit", "Please wait 42 seconds before requesting another code."),
		);
		await wrapper.find("#login-mobile").setValue("98765 43210");
		await wrapper.find("form").trigger("submit");
		await flushPromises();
		expect(wrapper.find("#otp-help").exists()).toBe(false);
		expect(wrapper.text()).toContain("Please wait 42 seconds");
		wrapper.unmount();
	});
});
