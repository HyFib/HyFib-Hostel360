/** Client-side rules that must agree with the server: mess days, the hostel clock, uploads, checkout forms, access. */
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RouteLocationNormalized } from "vue-router";
import { acceptAttribute, checkAttachment, uploadRules } from "@/api/files";
import LeaveForm from "@/components/LeaveForm.vue";
import { decideNavigation, type GuardState } from "@/router/guards";
import { routes } from "@/router/routes";
import { applySession, resetSession, useSession } from "@/stores/session";
import { canPay } from "@/utils/fees";
import { deviceOffSiteTime, messDaysAway, siteNow } from "@/utils/format";
import { buildRedirectForm, RedirectCheckoutError } from "@/utils/redirectCheckout";

afterEach(() => {
	vi.useRealTimers();
	resetSession();
});

describe("messDaysAway (the server's noon rule)", () => {
	it("counts only full mess days away", () => {
		// Friday 6 pm to Monday 10 am: Saturday and Sunday.
		expect(messDaysAway("2026-10-16 18:00:00", "2026-10-19 10:00:00")).toBe(2);
		// Leaving before noon and returning after noon counts both ends.
		expect(messDaysAway("2026-10-16 09:00:00", "2026-10-19 13:00:00")).toBe(4);
		expect(messDaysAway("2026-10-16 18:00:00", "2026-10-17 10:00:00")).toBe(0);
		expect(messDaysAway("2026-10-16 18:00:00", "2026-10-16 08:00:00")).toBe(0);
		expect(messDaysAway("2026-10-16 10:00:00", "2026-10-19 10:00:00", 9)).toBe(3);
	});
});

describe("the hostel clock", () => {
	it("reads India time whatever the phone's zone", () => {
		const instant = new Date(Date.UTC(2026, 9, 15, 12, 0, 0));
		const india = siteNow("Asia/Kolkata", instant);
		expect([india.getHours(), india.getMinutes()]).toEqual([17, 30]);
		const singapore = siteNow("Asia/Singapore", instant);
		expect(singapore.getHours()).toBe(20);
	});

	it("knows when the phone is not on India time", () => {
		const instant = new Date();
		const offset = siteNow("Asia/Kolkata", instant).getTime() - instant.getTime();
		expect(deviceOffSiteTime("Asia/Kolkata", instant)).toBe(Math.abs(offset) > 60_000);
	});

	it("is what the leave form checks 'in the past' against", async () => {
		vi.useFakeTimers({ toFake: ["Date"] });
		vi.setSystemTime(new Date(Date.UTC(2026, 9, 15, 12, 0, 0))); // 17:30 in India
		const wrapper = mount(LeaveForm, { props: { reductionMinDays: 3, timeZone: "Asia/Kolkata" }, attachTo: document.body });
		await wrapper.find('[name="from_datetime"]').setValue("2026-10-15T15:00"); // two and a half hours ago in India
		await wrapper.find('[name="to_datetime"]').setValue("2026-10-18T15:00");
		await wrapper.find('[name="destination"]').setValue("Madurai");
		await wrapper.find('[name="reason"]').setValue("Home visit");
		await wrapper.find('[name="contact_during_leave"]').setValue("9876543210");
		await wrapper.find("form").trigger("submit");
		expect(wrapper.text()).toContain("The leaving time can't be in the past.");
		expect(wrapper.emitted("submit")).toBeUndefined();
		wrapper.unmount();
	});
});

describe("LeaveForm mess reduction", () => {
	it("does not offer a reduction the server would skip (Friday evening to Monday morning)", async () => {
		const wrapper = mount(LeaveForm, {
			props: { reductionMinDays: 3, cutoffHour: 12, now: () => new Date(2026, 9, 15, 9, 0, 0) },
			attachTo: document.body,
		});
		await wrapper.find('[name="from_datetime"]').setValue("2026-10-16T18:00");
		await wrapper.find('[name="to_datetime"]').setValue("2026-10-19T10:00");
		await wrapper.find('[name="destination"]').setValue("Madurai");
		await wrapper.find('[name="reason"]').setValue("Home visit");
		await wrapper.find('[name="contact_during_leave"]').setValue("9876543210");

		expect(wrapper.text()).toContain("4 days");
		expect(wrapper.text()).toContain("only 2 full mess days");
		expect(wrapper.find('[role="switch"]').attributes("disabled")).toBeDefined();
		await wrapper.find("form").trigger("submit");
		expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({ apply_mess_reduction: 0 });
		wrapper.unmount();
	});
});

describe("attachments", () => {
	const pdf = (megabytes: number) =>
		new File([new Uint8Array(Math.round(megabytes * 1024 * 1024))], "scan.pdf", { type: "application/pdf" });

	it("are refused before upload when the server would refuse them", () => {
		const rules = uploadRules({ max_bytes: 5 * 1024 * 1024, extensions: ["pdf", "jpg", "jpeg", "png", "webp"] });
		expect(checkAttachment(pdf(4.9), rules)).toBeNull();
		expect(checkAttachment(pdf(7), rules)).toContain("5.0 MB");
		expect(checkAttachment(new File(["GIF89a"], "funny.gif", { type: "image/gif" }), rules)).not.toBeNull();
		expect(checkAttachment(new File(["x"], "setup.exe", { type: "application/pdf" }), rules)).not.toBeNull();
		expect(acceptAttribute(rules)).toBe("application/pdf,image/jpeg,image/png,image/webp");
	});

	it("default to the server's 5 MB limit before the session says otherwise", () => {
		expect(uploadRules().maxBytes).toBe(5 * 1024 * 1024);
		expect(uploadRules().extensions).not.toContain("gif");
		expect(checkAttachment(pdf(6))).not.toBeNull();
	});
});

describe("redirect checkout form", () => {
	it("carries every signed field to the gateway", () => {
		const form = buildRedirectForm({
			action_url: "https://secure.payu.in/_payment",
			method: "POST",
			fields: { key: "K", txnid: "T1", amount: "1500.00", firstname: "Priya", hash: "h" },
		});
		expect(form.action).toBe("https://secure.payu.in/_payment");
		expect(form.method).toBe("post");
		const data = new FormData(form);
		expect([...data.keys()]).toEqual(["key", "txnid", "amount", "firstname", "hash"]);
		expect(data.get("amount")).toBe("1500.00");
		expect(form.querySelectorAll('input[type="hidden"]')).toHaveLength(5);
	});

	it("refuses a payment page that is not HTTPS", () => {
		expect(() => buildRedirectForm({ action_url: "http://test.payu.in/_payment", method: "POST", fields: {} })).toThrow(
			RedirectCheckoutError,
		);
		expect(() => buildRedirectForm({ action_url: "not a url", method: "POST", fields: {} })).toThrow(RedirectCheckoutError);
	});
});

describe("payable invoices", () => {
	it("stay payable while an abandoned checkout is still open", () => {
		const base = { name: "INV-1", invoice_type: "Hostel Fee", posting_date: "2026-10-01", total_amount: 100, paid_amount: 0, items: [] };
		expect(canPay({ ...base, outstanding_amount: 100, status: "Unpaid", payment_in_progress: true })).toBe(true);
		expect(canPay({ ...base, outstanding_amount: 100, status: "Cancelled" })).toBe(false);
		expect(canPay({ ...base, outstanding_amount: 0, status: "Paid" })).toBe(false);
	});
});

describe("access by ward", () => {
	function route(name: string, fullPath: string): RouteLocationNormalized {
		const record = routes.find((item) => item.name === name);
		return { name, fullPath, path: fullPath, query: {}, params: {}, hash: "", matched: [], meta: record?.meta ?? {}, redirectedFrom: undefined };
	}
	const validated: GuardState = { loggedIn: true, kind: "student", consentRequired: false, profileConfirmed: true, validationStatus: "Validated" };

	it("keeps a validated student who is not Active to their dues", () => {
		const suspended = { ...validated, studentStatus: "Suspended" };
		expect(decideNavigation(route("leave", "/leave"), suspended)).toEqual({ name: "home", query: { notice: "restricted" } });
		expect(decideNavigation(route("grievances", "/grievances"), suspended)).toEqual({ name: "home", query: { notice: "restricted" } });
		expect(decideNavigation(route("fees", "/fees"), suspended)).toBe(true);
		expect(decideNavigation(route("pay", "/fees/pay"), suspended)).toBe(true);
		expect(decideNavigation(route("receipts", "/receipts"), suspended)).toBe(true);
		expect(decideNavigation(route("leave", "/leave"), { ...validated, studentStatus: "Active" })).toBe(true);
	});

	it("judges a parent's pages by the selected ward, not the guardian record", () => {
		applySession({
			user: "parent@portal.hostel360.invalid",
			kind: "parent",
			display_name: "Raman K",
			validation_status: "Validated",
			wards: [
				{ student: "REG001", full_name: "Priya", validation_status: "Validated", status: "Active" },
				{ student: "REG002", full_name: "Arun", validation_status: "Pending", status: "Active" },
			],
			settings: {},
		});
		const { effectiveValidationStatus, hasFullAccess, setActiveStudent } = useSession();
		expect(effectiveValidationStatus.value).toBe("Validated");
		setActiveStudent("REG002");
		expect(effectiveValidationStatus.value).toBe("Pending");
		expect(hasFullAccess.value).toBe(false);
	});
});

describe("LoginView", () => {
	beforeEach(() => {
		resetSession();
	});

	it("goes back to the code step when the code expires on the choose step", async () => {
		vi.resetModules();
		vi.doMock("@/api/auth", async () => {
			const { ApiError } = await import("@/api/client");
			return {
				sendOtp: vi.fn(() => Promise.resolve({ sent: true, masked: "98xxxxxx10", resend_after: 60, expires_in: 300 })),
				verifyOtp: vi
					.fn()
					.mockResolvedValueOnce({ ok: false, choose: ["student", "parent"] })
					.mockRejectedValueOnce(
						new ApiError("auth", "This code is not valid or has expired. Please request a new code.", {
							status: 401,
							excType: "AuthenticationError",
						}),
					),
				logout: vi.fn(),
			};
		});
		const { createMemoryHistory, createRouter } = await import("vue-router");
		const { routes: freshRoutes } = await import("@/router/routes");
		const LoginView = (await import("@/views/LoginView.vue")).default;
		const OtpInput = (await import("@/components/OtpInput.vue")).default;
		const router = createRouter({ history: createMemoryHistory("/hostel/"), routes: freshRoutes });
		await router.push("/login");
		await router.isReady();
		const wrapper = mount(LoginView, { global: { plugins: [router] }, attachTo: document.body });

		await wrapper.find("#login-mobile").setValue("9876543210");
		await wrapper.find("form").trigger("submit");
		await flushPromises();
		const otp = wrapper.findComponent(OtpInput);
		otp.vm.$emit("update:modelValue", "246810");
		await flushPromises();
		await wrapper.find("form").trigger("submit");
		await flushPromises();
		expect(wrapper.text()).toContain("I am a parent or guardian");

		await wrapper.findAll("button").find((button) => button.text().includes("I am a parent"))!.trigger("click");
		await flushPromises();
		// Back on the code step, where Resend and Change number are.
		expect(wrapper.findComponent(OtpInput).exists()).toBe(true);
		expect(wrapper.text()).toContain("This code is not valid or has expired.");
		expect(wrapper.text()).toContain("Change number");
		wrapper.unmount();
		vi.doUnmock("@/api/auth");
	});
});
