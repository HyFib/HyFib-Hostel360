/**
 * Pages that show what the server sends: the privacy notice, payment outcomes, the home
 * screen, grievances, notifications and leave. The portal API is replaced by mocks; the
 * session store is filled the way get_session fills it.
 */
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter, type Router } from "vue-router";
import { ApiError } from "@/api/client";
import type { Dashboard, Grievance, Leave, PortalNotification, PortalSession } from "@/api/types";

vi.mock("@/api/portal", () => ({
	getSession: vi.fn(),
	setLanguage: vi.fn(() => Promise.resolve({ ok: true, language: "en" })),
	getPrivacyNotice: vi.fn(),
	acceptPrivacyNotice: vi.fn(),
	getDashboard: vi.fn(),
	getFees: vi.fn(),
	getPaymentStatus: vi.fn(),
	createPayment: vi.fn(),
	verifyPayment: vi.fn(),
	downloadReceipt: vi.fn(),
	downloadFeeStructure: vi.fn(),
	getGrievance: vi.fn(),
	commentGrievance: vi.fn(),
	reopenGrievance: vi.fn(),
	rateGrievance: vi.fn(),
	raiseGrievance: vi.fn(),
	uploadAttachment: vi.fn(),
	listNotifications: vi.fn(),
	markNotificationsRead: vi.fn(() => Promise.resolve({ ok: true })),
	listLeaves: vi.fn(),
	cancelLeave: vi.fn(),
}));
vi.mock("@/api/auth", () => ({ logout: vi.fn(() => Promise.resolve({})), sendOtp: vi.fn(), verifyOtp: vi.fn() }));
vi.mock("@/utils/razorpay", () => ({ openCheckout: vi.fn(), loadRazorpay: vi.fn() }));

const portal = await import("@/api/portal");
const auth = await import("@/api/auth");
const razorpay = await import("@/utils/razorpay");
const { routes } = await import("@/router/routes");
const { applySession, loadSession, resetSession, session } = await import("@/stores/session");
const { resetBoot, updateBoot } = await import("@/utils/boot");

function studentSession(overrides: Partial<PortalSession> = {}): PortalSession {
	return {
		user: "priya@portal.hostel360.invalid",
		kind: "student",
		display_name: "Priya Raman",
		wards: [{ student: "REG001", full_name: "Priya Raman", status: "Active", validation_status: "Validated" }],
		validation_status: "Validated",
		profile_confirmed: 1,
		consent_required: 0,
		privacy_notice_version: "1.0",
		language: "en",
		settings: { payment_enabled: true },
		...overrides,
	};
}

function parentSession(overrides: Partial<PortalSession> = {}): PortalSession {
	return studentSession({
		user: "parent@portal.hostel360.invalid",
		kind: "parent",
		display_name: "Raman K",
		wards: [
			{ student: "REG001", full_name: "Priya Raman", status: "Active", validation_status: "Validated" },
			{ student: "REG002", full_name: "Arun Raman", status: "Active", validation_status: "Validated" },
		],
		...overrides,
	});
}

let router: Router;

async function mountAt(path: string, component: unknown) {
	router = createRouter({ history: createMemoryHistory("/hostel/"), routes });
	await router.push(path);
	await router.isReady();
	const wrapper = mount(component as never, { global: { plugins: [router] }, attachTo: document.body });
	await flushPromises();
	return wrapper;
}

beforeEach(() => {
	resetSession();
	resetBoot();
	vi.clearAllMocks();
});

afterEach(() => {
	vi.useRealTimers();
});

describe("PrivacyView", () => {
	const PrivacyView = () => import("@/views/PrivacyView.vue").then((m) => m.default);

	it("shows the institution's notice and records consent to the version it showed", async () => {
		applySession(studentSession({ consent_required: 1, privacy_notice_version: "1.0" }));
		vi.mocked(portal.getPrivacyNotice).mockResolvedValue({
			version: "2.1",
			notice: "<h2>Grievance Officer</h2><p>Dr. Meena, hostel office.</p><script>alert(1)</script>",
			consent_required: 1,
		});
		vi.mocked(portal.acceptPrivacyNotice).mockResolvedValue({ ok: true });

		const wrapper = await mountAt("/privacy", await PrivacyView());
		expect(wrapper.find('[data-test="notice"]').text()).toContain("Dr. Meena, hostel office.");
		expect(wrapper.html()).not.toContain("<script");
		expect(wrapper.find('[data-test="notice-default"]').exists()).toBe(false);
		expect(wrapper.text()).toContain("2.1");

		await wrapper.find('[data-test="accept"]').trigger("click");
		await flushPromises();
		expect(portal.acceptPrivacyNotice).toHaveBeenCalledWith("2.1");
		wrapper.unmount();
	});

	it("keeps Accept off while the notice is loading or could not be loaded", async () => {
		applySession(studentSession({ consent_required: 1 }));
		let fail: (error: unknown) => void = () => undefined;
		vi.mocked(portal.getPrivacyNotice).mockReturnValue(new Promise((_, reject) => (fail = reject)));

		const wrapper = await mountAt("/privacy", await PrivacyView());
		expect(wrapper.find('[data-test="accept"]').attributes("disabled")).toBeDefined();

		fail(new ApiError("offline", "We couldn't reach the hostel server."));
		await flushPromises();
		expect(wrapper.find('[data-test="notice-error"]').exists()).toBe(true);
		expect(wrapper.find('[data-test="notice-default"]').exists()).toBe(false);
		expect(wrapper.find('[data-test="accept"]').attributes("disabled")).toBeDefined();
		await wrapper.find('[data-test="accept"]').trigger("click");
		expect(portal.acceptPrivacyNotice).not.toHaveBeenCalled();
		wrapper.unmount();
	});
});

describe("FeesView after a PayU checkout", () => {
	const FeesView = () => import("@/views/FeesView.vue").then((m) => m.default);
	const emptyFees = { outstanding: 0, overdue: 0, invoices: [], payments: [] };

	it("shows the outcome the server confirms, not the one in the address", async () => {
		applySession(studentSession());
		vi.mocked(portal.getFees).mockResolvedValue(emptyFees);
		vi.mocked(portal.getPaymentStatus).mockResolvedValue({
			status: "Failed",
			payment: null,
			receipt_number: null,
			transaction: "PTX-2026-000042",
			message: "The payment did not go through. You can try again.",
		});

		const wrapper = await mountAt("/fees?payment=PTX-2026-000042&status=success", await FeesView());
		expect(portal.getPaymentStatus).toHaveBeenCalledWith("PTX-2026-000042");
		const outcome = wrapper.find('[data-test="payment-outcome"]');
		expect(outcome.attributes("data-outcome")).toBe("failed");
		expect(outcome.text()).toContain("The payment did not go through.");
		// The parameters are dropped so a reload never replays the outcome.
		expect(router.currentRoute.value.query).toEqual({});
		wrapper.unmount();
	});

	it("does not claim a payment it cannot find is being confirmed", async () => {
		applySession(studentSession());
		vi.mocked(portal.getFees).mockResolvedValue(emptyFees);
		vi.mocked(portal.getPaymentStatus).mockRejectedValue(
			new ApiError("not_found", "Payment PTX-NOPE was not found.", { status: 404, excType: "DoesNotExistError" }),
		);
		const wrapper = await mountAt("/fees?payment=PTX-NOPE&status=success", await FeesView());
		const outcome = wrapper.find('[data-test="payment-outcome"]');
		expect(outcome.attributes("data-outcome")).toBe("failed");
		expect(outcome.text()).toContain("couldn't match");
		wrapper.unmount();
	});

	it("says plainly that online payment is off on a demonstration site", async () => {
		applySession(studentSession({ settings: { payment_enabled: false, demo: true } }));
		vi.mocked(portal.getFees).mockResolvedValue({
			outstanding: 1500,
			overdue: 0,
			payments: [],
			payment_enabled: false,
			invoices: [
				{
					name: "INV-1",
					invoice_type: "Hostel Fee",
					posting_date: "2026-10-01",
					total_amount: 1500,
					paid_amount: 0,
					outstanding_amount: 1500,
					status: "Unpaid",
					items: [],
				},
			],
		});
		const wrapper = await mountAt("/fees", await FeesView());
		expect(wrapper.text()).toContain("Online payment is switched off on this demonstration site");
		expect(wrapper.text()).not.toContain("Online payment isn't available right now");
		wrapper.unmount();
	});

	it("offers the receipt when the payment succeeded", async () => {
		applySession(studentSession());
		vi.mocked(portal.getFees).mockResolvedValue(emptyFees);
		vi.mocked(portal.getPaymentStatus).mockResolvedValue({
			status: "Success",
			payment: "RCPT-2026-00007",
			receipt_number: "RCPT-2026-00007",
			transaction: "PTX-2026-000043",
		});
		const wrapper = await mountAt("/fees?payment=PTX-2026-000043&status=success", await FeesView());
		const outcome = wrapper.find('[data-test="payment-outcome"]');
		expect(outcome.attributes("data-outcome")).toBe("success");
		expect(outcome.text()).toContain("RCPT-2026-00007");
		wrapper.unmount();
	});
});

describe("PayView with PayU", () => {
	const PayView = () => import("@/views/PayView.vue").then((m) => m.default);

	it("posts the server-signed form to PayU's page", async () => {
		applySession(studentSession());
		vi.mocked(portal.getFees).mockResolvedValue({
			outstanding: 1500,
			overdue: 0,
			payments: [],
			invoices: [
				{
					name: "INV-1",
					invoice_type: "Hostel Fee",
					posting_date: "2026-10-01",
					total_amount: 1500,
					paid_amount: 0,
					outstanding_amount: 1500,
					status: "Unpaid",
					items: [],
					payment_in_progress: true,
				},
			],
		});
		vi.mocked(portal.createPayment).mockResolvedValue({
			transaction: "PTX-1",
			gateway: "payu",
			amount: 1500,
			checkout: {
				action_url: "https://test.payu.in/_payment",
				method: "POST",
				fields: { key: "gtKFFx", txnid: "PTX1ABC", amount: "1500.00", hash: "abc123", udf1: "PTX-1" },
			},
		});
		const submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => undefined);

		const wrapper = await mountAt("/fees/pay", await PayView());
		// An invoice whose earlier checkout was closed is still payable (the same order is reused).
		expect(wrapper.text()).toContain("Continue paying");
		await wrapper.find("button.btn-primary").trigger("click");
		await flushPromises();

		expect(submit).toHaveBeenCalledTimes(1);
		const form = submit.mock.contexts[0] as HTMLFormElement;
		expect(form.action).toBe("https://test.payu.in/_payment");
		expect(form.method).toBe("post");
		expect(new FormData(form).get("hash")).toBe("abc123");
		expect(new FormData(form).get("udf1")).toBe("PTX-1");
		expect(wrapper.text()).toContain("Opening the payment page");
		wrapper.unmount();
	});
});

describe("PayView in test mode", () => {
	const PayView = () => import("@/views/PayView.vue").then((m) => m.default);
	const invoice = {
		name: "INV-2",
		invoice_type: "Hostel Fee",
		posting_date: "2026-10-01",
		total_amount: 1500,
		paid_amount: 0,
		outstanding_amount: 1500,
		status: "Unpaid",
		items: [],
	};

	it("says no real money is taken before the payer starts", async () => {
		applySession(studentSession());
		vi.mocked(portal.getFees).mockResolvedValue({
			outstanding: 1500,
			overdue: 0,
			payments: [],
			invoices: [invoice],
			payment_enabled: true,
			payment_test_mode: true,
		});
		const wrapper = await mountAt("/fees/pay", await PayView());
		expect(wrapper.find('[data-test="test-mode"]').text()).toContain("No real money is taken");
		wrapper.unmount();
	});

	it("keeps the dues open after a failed attempt and marks it as a test", async () => {
		applySession(studentSession());
		vi.mocked(portal.getFees).mockResolvedValue({
			outstanding: 1500,
			overdue: 0,
			payments: [],
			invoices: [invoice],
			payment_enabled: true,
		});
		vi.mocked(portal.createPayment).mockResolvedValue({
			transaction: "PTX-9",
			gateway: "razorpay",
			test_mode: 1,
			checkout: {
				key: "rzp_test_x",
				order_id: "order_9",
				amount: 150000,
				currency: "INR",
				name: "ACGCET",
				description: "Hostel dues",
				prefill: {},
			},
		});
		vi.mocked(razorpay.openCheckout).mockResolvedValue({
			type: "success",
			response: { razorpay_payment_id: "pay_9", razorpay_order_id: "order_9", razorpay_signature: "sig" },
		});
		vi.mocked(portal.verifyPayment).mockResolvedValue({
			status: "Failed",
			payment: null,
			receipt_number: null,
			transaction: "PTX-9",
			message: "Your bank declined the payment.",
		});

		const wrapper = await mountAt("/fees/pay", await PayView());
		expect(wrapper.find('[data-test="test-mode"]').exists()).toBe(false);
		await wrapper.find("button.btn-primary").trigger("click");
		await flushPromises();

		const outcome = wrapper.find('[data-test="payment-outcome"]');
		expect(outcome.attributes("data-outcome")).toBe("failed");
		expect(outcome.text()).toContain("Your bank declined the payment.");
		expect(outcome.find('[data-test="dues-still-open"]').exists()).toBe(true);
		expect(outcome.find('[data-test="test-outcome"]').text()).toContain("test payment");
		wrapper.unmount();
	});
});

describe("HomeView", () => {
	const HomeView = () => import("@/views/HomeView.vue").then((m) => m.default);

	function dashboard(overrides: Partial<Dashboard> = {}): Dashboard {
		return {
			student: "REG001",
			room: null,
			fees: { outstanding: 0, overdue: 0 },
			today_menu: null,
			pending: {},
			notifications: [],
			unavailable: [],
			...overrides,
		};
	}

	it("never shows 'no dues' when the dues could not be loaded", async () => {
		applySession(studentSession());
		vi.mocked(portal.getDashboard).mockResolvedValue(dashboard({ fees: null, unavailable: ["fees"] }));
		const wrapper = await mountAt("/", await HomeView());
		expect(wrapper.find('[data-test="dues-unavailable"]').exists()).toBe(true);
		expect(wrapper.text()).not.toContain("You have no pending dues.");
		expect(wrapper.text()).not.toContain("₹0");
		wrapper.unmount();
	});

	it("tells screen readers which updates are unread and shows the details", async () => {
		applySession(studentSession());
		vi.mocked(portal.getDashboard).mockResolvedValue(
			dashboard({
				notifications: [
					{
						name: "N1",
						subject: "Leave rejected",
						message: "LV-0001: Remarks: exams",
						created: "2026-10-15 10:00:00",
						read: 0,
					},
					{ name: "N2", subject: "Fee due", created: "2026-10-14 10:00:00", read: 1 },
				],
			}),
		);
		const wrapper = await mountAt("/", await HomeView());
		const items = wrapper.findAll('[aria-labelledby="home-updates"] li');
		expect(items[0].find(".sr-only").text()).toContain("Unread");
		expect(items[0].text()).toContain("LV-0001: Remarks: exams");
		expect(items[1].find(".sr-only").exists()).toBe(false);
		wrapper.unmount();
	});

	it("explains a suspended student's limits instead of showing resident features", async () => {
		applySession(
			studentSession({
				wards: [{ student: "REG001", full_name: "Priya Raman", status: "Suspended", validation_status: "Validated" }],
			}),
		);
		vi.mocked(portal.getDashboard).mockResolvedValue(dashboard({ restricted: true, student_status: "Suspended" }));
		const wrapper = await mountAt("/", await HomeView());
		expect(wrapper.find('[data-test="restricted-notice"]').text()).toContain("Suspended");
		expect(wrapper.find("#home-menu").exists()).toBe(false);
		expect(wrapper.find("#home-actions").exists()).toBe(false);
		wrapper.unmount();
	});

	it("opens a parent's notification for the ward it is about", async () => {
		applySession(parentSession());
		const note: PortalNotification = {
			name: "N9",
			subject: "Leave approved",
			created: "2026-10-15 10:00:00",
			read: 0,
			link: "/hostel/leave",
			student: "REG002",
		};
		vi.mocked(portal.getDashboard).mockResolvedValue(dashboard({ notifications: [note] }));
		const wrapper = await mountAt("/", await HomeView());
		expect(session.activeStudent).toBe("REG001");
		await wrapper.find('[aria-labelledby="home-updates"] li a').trigger("click");
		await flushPromises();
		expect(session.activeStudent).toBe("REG002");
		// The leave page is loaded on demand; wait for the navigation to finish.
		await vi.waitFor(() => expect(router.currentRoute.value.path).toBe("/leave"));
		wrapper.unmount();
	});
});

describe("GrievanceDetailView", () => {
	const GrievanceDetailView = () => import("@/views/GrievanceDetailView.vue").then((m) => m.default);

	function grievance(): Grievance {
		return {
			name: "GRV-2026-00001",
			subject: "No hot water",
			category: "Water",
			priority: "Medium",
			status: "Open",
			creation: "2026-10-15 09:00:00",
			description: "Geyser not working for a week.",
			attachments: [
				{
					name: "f1a2b3",
					file_name: "geyser.jpg",
					file_size: 2048,
					url: "/api/method/hostel360.api.portal.download_grievance_attachment?name=GRV-2026-00001&file=f1a2b3",
				},
			],
			timeline: [
				{ on: "2026-10-15 09:00:00", by: "Raman K", is_staff: false, is_mine: false, message: "Grievance registered." },
				{
					on: "2026-10-15 09:05:00",
					by: "Raman K",
					is_staff: false,
					is_mine: false,
					message: "Please fix this quickly.",
				},
				{ on: "2026-10-15 10:00:00", by: "Priya Raman", is_staff: false, is_mine: true, message: "Still not fixed." },
			],
		};
	}

	it("labels only the viewer's own entries as theirs and links attachments through the portal", async () => {
		applySession(studentSession());
		vi.mocked(portal.getGrievance).mockResolvedValue(grievance());
		const wrapper = await mountAt("/grievances/GRV-2026-00001", await GrievanceDetailView());

		const entries = wrapper.findAll("ol li");
		const authors = Object.fromEntries(
			entries.map((entry) => [entry.find(".whitespace-pre-line").text(), entry.find(".font-semibold").text()]),
		);
		expect(authors["Please fix this quickly."]).toBe("Raman K");
		expect(authors["Grievance registered."]).toBe("Raman K");
		expect(authors["Still not fixed."]).toBe("You");

		const link = wrapper.find('a[href*="download_grievance_attachment"]');
		expect(link.exists()).toBe(true);
		expect(wrapper.find('a[href^="/private/files"]').exists()).toBe(false);
		wrapper.unmount();
	});

	it("shows each entry in the reader's language and typed text as it was written", async () => {
		applySession(studentSession());
		vi.mocked(portal.getGrievance).mockResolvedValue({
			...grievance(),
			timeline: [
				{
					id: "u1",
					on: "2026-10-15 09:00:00",
					by: "Hostel Office",
					is_staff: true,
					message: "Assigned to Murugan K.",
					display_message: "Murugan K.-இடம் ஒப்படைக்கப்பட்டது.",
				},
				{
					id: "u2",
					on: "2026-10-15 09:05:00",
					by: "Priya Raman",
					is_mine: true,
					message: "Closed",
					display_message: "Closed",
				},
				// An older server that does not send display_message.
				{ id: "u3", on: "2026-10-15 09:10:00", by: "Hostel Office", is_staff: true, message: "Grievance registered." },
			],
		});
		const wrapper = await mountAt("/grievances/GRV-2026-00001", await GrievanceDetailView());

		const texts = wrapper.findAll("ol li .whitespace-pre-line").map((node) => node.text());
		expect(texts.sort()).toEqual(["Closed", "Grievance registered.", "Murugan K.-இடம் ஒப்படைக்கப்பட்டது."]);
		expect(wrapper.text()).not.toContain("Assigned to Murugan K.");
		wrapper.unmount();
	});
});

describe("GrievanceNewView", () => {
	const GrievanceNewView = () => import("@/views/GrievanceNewView.vue").then((m) => m.default);

	it("offers the hostel office and public helplines for ragging, never the portal helpdesk", async () => {
		applySession(studentSession({ settings: { office_phone: "04565 224528", support_phone: "+91 96060 60596" } }));
		const wrapper = await mountAt("/grievances/new", await GrievanceNewView());
		await wrapper.find("select").setValue("Ragging or Harassment");
		await flushPromises();
		const numbers = wrapper.find('[data-test="emergency-numbers"]').html();
		expect(numbers).toContain("tel:04565224528");
		expect(numbers).toContain("tel:112");
		expect(numbers).not.toContain("96060");
		wrapper.unmount();
	});

	it("advertises the server's upload limit", async () => {
		applySession(
			studentSession({
				settings: { upload: { max_bytes: 5 * 1024 * 1024, extensions: ["pdf", "jpg", "jpeg", "png", "webp"] } },
			}),
		);
		const wrapper = await mountAt("/grievances/new", await GrievanceNewView());
		expect(wrapper.find("#attach-hint").text()).toContain("5.0 MB");
		expect(wrapper.find('input[type="file"]').attributes("accept")).not.toContain("gif");
		wrapper.unmount();
	});
});

describe("LeaveView", () => {
	const LeaveView = () => import("@/views/LeaveView.vue").then((m) => m.default);

	it("says when a requested mess reduction was not applied", async () => {
		applySession(studentSession({ settings: { mess_reduction_min_days: 3 } }));
		const leave: Leave = {
			name: "LV-1",
			from_datetime: "2026-10-16 18:00:00",
			to_datetime: "2026-10-19 10:00:00",
			destination: "Madurai",
			reason: "Home visit",
			status: "Approved",
			apply_mess_reduction: 1,
			mess_reduction_days: 2,
			mess_reduction: null,
		};
		vi.mocked(portal.listLeaves).mockResolvedValue([leave]);
		const wrapper = await mountAt("/leave", await LeaveView());
		await wrapper.find('button[aria-controls="leave-LV-1"]').trigger("click");
		const notice = wrapper.find('[data-test="reduction-not-applied"]');
		expect(notice.text()).toContain("2 full mess days");
		expect(notice.text()).toContain("at least 3");
		wrapper.unmount();
	});
});

describe("AccessClosedView", () => {
	const AccessClosedView = () => import("@/views/AccessClosedView.vue").then((m) => m.default);

	it("says why the session ended and how to reach the hostel office", async () => {
		updateBoot({ office_phone: "04565 224528", office_email: "hostel.office@example.com" });
		vi.mocked(portal.getSession).mockRejectedValue(
			new ApiError(
				"permission",
				"Portal access for this hostel record has been closed. Please contact the hostel office.",
				{
					status: 403,
					excType: "PortalAccessClosed",
				},
			),
		);
		await loadSession(true);

		const wrapper = await mountAt("/access-closed", await AccessClosedView());
		expect(wrapper.find('[data-test="closed-reason"]').text()).toContain("has been closed");
		const office = wrapper.find('[data-test="closed-office"]');
		expect(office.find('a[href="tel:04565224528"]').exists()).toBe(true);
		expect(office.find('a[href="mailto:hostel.office@example.com"]').exists()).toBe(true);

		await wrapper.find('[data-test="sign-in-again"]').trigger("click");
		// The sign-in page is loaded on demand.
		await vi.waitFor(() => expect(router.currentRoute.value.name).toBe("login"), { timeout: 5000 });
		await vi.waitFor(() => expect(session.closedReason).toBe(""));
		wrapper.unmount();
	});
});

describe("session refused by the server", () => {
	it("signs out and keeps the reason for the sign-in page", async () => {
		vi.mocked(portal.getSession).mockRejectedValue(
			new ApiError("permission", "Your hostel registration has not been validated by the hostel office yet.", {
				status: 403,
				excType: "PortalAccessClosed",
			}),
		);
		await loadSession(true);
		expect(session.loggedIn).toBe(false);
		expect(session.closedReason).toContain("not been validated");
		expect(auth.logout).toHaveBeenCalledTimes(1);
	});
});

describe("WardSwitcher", () => {
	const WardSwitcher = () => import("@/components/WardSwitcher.vue").then((m) => m.default);

	it("names hostels and rooms, marks a ward still being verified, and leaves pages that ward cannot use", async () => {
		applySession(
			parentSession({
				wards: [
					{
						student: "REG001",
						full_name: "Priya Raman",
						register_number: "UAT24CS0012",
						hostel: "GH1",
						hostel_name: "Girls Hostel I",
						room: "GH1-A003",
						room_number: "A003",
						status: "Active",
						validation_status: "Validated",
					},
					{
						student: "REG002",
						full_name: "Arun Raman",
						register_number: "UAT26ME0007",
						status: "Applied",
						validation_status: "Pending",
					},
				],
			}),
		);
		const wrapper = await mountAt("/leave", await WardSwitcher());
		await wrapper.find("button").trigger("click");
		await flushPromises();
		const text = document.body.textContent ?? "";
		expect(text).toContain("UAT24CS0012 · Girls Hostel I · A003");
		expect(text).not.toContain("GH1-A003");
		expect(text).toContain("Being verified");

		const arun = [...document.body.querySelectorAll("button")].find((button) =>
			button.textContent?.includes("Arun Raman"),
		);
		arun!.click();
		await flushPromises();
		expect(session.activeStudent).toBe("REG002");
		await vi.waitFor(() => expect(router.currentRoute.value.query.notice).toBe("verification"));
		wrapper.unmount();
	});
});

describe("attachment uploads", () => {
	it("send the ward the grievance is for", async () => {
		const actual = await vi.importActual<typeof import("@/api/portal")>("@/api/portal");
		const fetchMock = vi.fn(() =>
			Promise.resolve(
				new Response(JSON.stringify({ message: { file_url: "/private/files/a.png", file_name: "a.png" } })),
			),
		);
		vi.stubGlobal("fetch", fetchMock);
		await actual.uploadAttachment(new File(["x"], "a.png", { type: "image/png" }), "REG002");
		const body = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
		expect(body.get("student")).toBe("REG002");
		expect(body.get("purpose")).toBe("grievance");
		vi.unstubAllGlobals();
	});
});
