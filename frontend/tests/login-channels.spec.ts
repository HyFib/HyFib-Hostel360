/** Sign-in code channels on the login page: E-mail only when the site allows it. */
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetSession } from "@/stores/session";
import { resetBoot } from "@/utils/boot";

const SENT = { sent: true, masked: "98xxxxxx10", resend_after: 60, expires_in: 300 };

function setBoot(values: Record<string, unknown>) {
	document.body.innerHTML = `<script type="application/json" id="h360-boot">${JSON.stringify(values)}</script>`;
	resetBoot();
}

async function mountLogin() {
	const sendOtp = vi.fn(() => Promise.resolve(SENT));
	vi.resetModules();
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

function channelValues(wrapper: Awaited<ReturnType<typeof mountLogin>>["wrapper"]): string[] {
	return wrapper.findAll<HTMLInputElement>('input[name="channel"]').map((input) => input.element.value);
}

describe("LoginView sign-in code channels", () => {
	beforeEach(() => {
		resetSession();
	});

	afterEach(() => {
		vi.doUnmock("@/api/auth");
		resetBoot();
	});

	it("offers SMS and WhatsApp only while e-mail codes are off", async () => {
		setBoot({ otp_email: false });
		const { wrapper } = await mountLogin();
		expect(channelValues(wrapper)).toEqual(["sms", "whatsapp"]);
		expect(wrapper.text()).not.toContain("E-mail");
		wrapper.unmount();
	});

	it("hides e-mail when the page carries no boot data", async () => {
		setBoot({});
		const { wrapper } = await mountLogin();
		expect(channelValues(wrapper)).toEqual(["sms", "whatsapp"]);
		wrapper.unmount();
	});

	it("offers E-mail as a third choice and sends the code by e-mail", async () => {
		setBoot({ otp_email: true });
		const { wrapper, sendOtp } = await mountLogin();
		expect(channelValues(wrapper)).toEqual(["sms", "whatsapp", "email"]);
		expect(wrapper.find('[data-test="email-hint"]').exists()).toBe(false);

		await wrapper.find('[data-test="channel-email"] input').setValue(true);
		// The hint says where the code goes before anything is sent.
		expect(wrapper.find('[data-test="email-hint"]').text()).toContain(
			"e-mail address the hostel office has for this mobile number",
		);

		await wrapper.find("#login-mobile").setValue("98765 43210");
		await wrapper.find("form").trigger("submit");
		await flushPromises();
		expect(sendOtp).toHaveBeenCalledWith("9876543210", "email");
		const help = wrapper.find("#otp-help").text();
		expect(help).toContain("98xxxxxx10");
		expect(help).toContain("e-mail address");
		expect(window.localStorage.getItem("h360.otp-channel")).toBe("email");
		wrapper.unmount();
	});

	it("falls back to SMS when the remembered choice is no longer offered", async () => {
		setBoot({ otp_email: true });
		const first = await mountLogin();
		await first.wrapper.find('[data-test="channel-email"] input').setValue(true);
		await first.wrapper.find("#login-mobile").setValue("9876543210");
		await first.wrapper.find("form").trigger("submit");
		await flushPromises();
		first.wrapper.unmount();

		setBoot({ otp_email: false });
		const { wrapper, sendOtp } = await mountLogin();
		await wrapper.find("#login-mobile").setValue("9876543210");
		await wrapper.find("form").trigger("submit");
		await flushPromises();
		expect(sendOtp).toHaveBeenCalledWith("9876543210", "sms");
		wrapper.unmount();
	});

	it("explains the e-mail code in Tamil too", async () => {
		setBoot({ otp_email: true });
		const { wrapper } = await mountLogin();
		// The instance the freshly imported view uses.
		const { setLocale } = await import("@/i18n");
		await setLocale("ta");
		await flushPromises();
		expect(wrapper.text()).toContain("மின்னஞ்சல்");
		await wrapper.find('[data-test="channel-email"] input').setValue(true);
		expect(wrapper.find('[data-test="email-hint"]').text()).toContain("விடுதி அலுவலகத்தில்");
		wrapper.unmount();
	});
});
