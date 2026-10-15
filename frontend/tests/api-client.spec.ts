import { beforeEach, describe, expect, it, vi } from "vitest";
import {
	ApiError,
	call,
	callFile,
	configureApi,
	getCsrfToken,
	initCsrf,
	parseServerMessages,
	readCsrfFromMeta,
	setCsrfToken,
} from "@/api/client";

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json", ...headers },
	});
}

function serverMessages(...messages: string[]): string {
	return JSON.stringify(messages.map((message) => JSON.stringify({ message, indicator: "red" })));
}

const fetchMock = vi.fn<typeof fetch>();

async function failure(promise: Promise<unknown>): Promise<ApiError> {
	try {
		await promise;
	} catch (caught) {
		if (caught instanceof ApiError) return caught;
		throw caught;
	}
	throw new Error("expected the call to fail");
}

beforeEach(() => {
	fetchMock.mockReset();
	vi.stubGlobal("fetch", fetchMock);
	document.head.innerHTML = '<meta name="csrf-token" content="token-123">';
	initCsrf();
	configureApi({
		onNetworkStatus: undefined,
		onForbidden: undefined,
		onSessionEnded: undefined,
		onAccessClosed: undefined,
		onConsentRequired: undefined,
	});
});

function lastRequest(index = 0) {
	const [url, init] = fetchMock.mock.calls[index];
	return {
		url: String(url),
		init: init as RequestInit,
		headers: (init as RequestInit).headers as Record<string, string>,
	};
}

describe("CSRF token", () => {
	it("is read from the page's meta tag and sent with writes", async () => {
		fetchMock.mockResolvedValueOnce(json({ message: { ok: true } }));
		const result = await call<{ ok: boolean }>("hostel360.api.portal.cancel_leave", { name: "LV-1" });

		expect(result).toEqual({ ok: true });
		const { url, init, headers } = lastRequest();
		expect(url).toBe("/api/method/hostel360.api.portal.cancel_leave");
		expect(init.method).toBe("POST");
		expect(init.credentials).toBe("include");
		expect(headers["X-Frappe-CSRF-Token"]).toBe("token-123");
		expect(headers["Content-Type"]).toBe("application/json");
		// `_lang` keeps the server's messages in the language on screen.
		expect(JSON.parse(String(init.body))).toEqual({ name: "LV-1", _lang: "en" });
	});

	it("is not sent with GET requests, whose arguments go in the query string", async () => {
		fetchMock.mockResolvedValueOnce(json({ message: [] }));
		await call("hostel360.api.portal.list_leaves", { student: "REG001", skip: undefined }, { method: "GET" });

		const { url, headers } = lastRequest();
		expect(url).toBe("/api/method/hostel360.api.portal.list_leaves?student=REG001&_lang=en");
		expect(headers["X-Frappe-CSRF-Token"]).toBeUndefined();
	});

	it("ignores an unrendered template placeholder", () => {
		document.head.innerHTML = '<meta name="csrf-token" content="{{ csrf_token }}">';
		expect(readCsrfFromMeta()).toBe("");
	});

	it("uses a token refreshed after sign-in", async () => {
		setCsrfToken("fresh-token");
		fetchMock.mockResolvedValueOnce(json({ message: { ok: true } }));
		await call("hostel360.api.portal.accept_privacy_notice", { version: "1.0" });
		expect(lastRequest().headers["X-Frappe-CSRF-Token"]).toBe("fresh-token");
		expect(document.querySelector('meta[name="csrf-token"]')?.getAttribute("content")).toBe("fresh-token");
	});

	it("fetches a new token and retries once when the server rejects the old one", async () => {
		fetchMock
			.mockResolvedValueOnce(json({ exc_type: "CSRFTokenError" }, 400))
			.mockResolvedValueOnce(json({ message: { csrf_token: "renewed" } }))
			.mockResolvedValueOnce(json({ message: { name: "GRV-1" } }));

		const result = await call<{ name: string }>("hostel360.api.portal.raise_grievance", { subject: "Fan" });

		expect(result).toEqual({ name: "GRV-1" });
		expect(fetchMock).toHaveBeenCalledTimes(3);
		expect(lastRequest(1).url).toBe("/api/method/hostel360.api.auth.get_csrf_token?_lang=en");
		expect(lastRequest(2).headers["X-Frappe-CSRF-Token"]).toBe("renewed");
		expect(getCsrfToken()).toBe("renewed");
	});
});

describe("error mapping", () => {
	it("turns a 417 ValidationError into the server's message without markup", async () => {
		fetchMock.mockResolvedValueOnce(
			json(
				{ exc_type: "ValidationError", _server_messages: serverMessages("Leave overlaps with <b>LV-2026-00012</b>.") },
				417,
			),
		);
		const error = await failure(call("hostel360.api.portal.apply_leave"));

		expect(error).toBeInstanceOf(ApiError);
		expect(error.kind).toBe("validation");
		expect(error.status).toBe(417);
		expect(error.message).toBe("Leave overlaps with LV-2026-00012.");
	});

	it("falls back to the exception text when there are no queued messages", async () => {
		fetchMock.mockResolvedValueOnce(
			json(
				{
					exc_type: "ValidationError",
					exception: "frappe.exceptions.ValidationError: Reduction must be at least 3 days",
				},
				417,
			),
		);
		const error = await failure(call("hostel360.api.portal.apply_mess_reduction"));
		expect(error.message).toBe("Reduction must be at least 3 days");
	});

	it("maps 403 to a permission error and tells the app", async () => {
		const onForbidden = vi.fn();
		configureApi({ onForbidden });
		fetchMock.mockResolvedValueOnce(json({ exc_type: "PermissionError" }, 403));

		const error = await failure(call("hostel360.api.portal.get_fees"));

		expect(error.kind).toBe("permission");
		expect(error.message).toBe("You don't have access to this.");
		expect(onForbidden).toHaveBeenCalledWith(error);
	});

	it("shows a friendly message for 429 rate limits", async () => {
		fetchMock.mockResolvedValueOnce(json({ exc_type: "RateLimitExceededError" }, 429));
		const error = await failure(call("hostel360.api.auth.send_otp"));
		expect(error.kind).toBe("rate_limit");
		expect(error.message).toBe("Too many attempts, try again in a minute");
	});

	it("replaces Frappe's generic rate-limit text but keeps a specific one", async () => {
		fetchMock
			.mockResolvedValueOnce(
				json(
					{
						_server_messages: serverMessages(
							"You hit the rate limit because of too many requests. Please try after sometime.",
						),
					},
					429,
				),
			)
			.mockResolvedValueOnce(
				json({ _server_messages: serverMessages("Please wait 42 seconds before requesting another code.") }, 429),
			);

		const generic = await failure(call("hostel360.api.auth.send_otp"));
		const specific = await failure(call("hostel360.api.auth.send_otp"));
		expect(generic.message).toBe("Too many attempts, try again in a minute");
		expect(specific.message).toBe("Please wait 42 seconds before requesting another code.");
	});

	it("keeps the sign-in message of a 401 AuthenticationError", async () => {
		fetchMock.mockResolvedValueOnce(
			json(
				{
					exc_type: "AuthenticationError",
					_server_messages: serverMessages("The code is incorrect. You have 2 more attempts left."),
				},
				401,
			),
		);
		const error = await failure(call("hostel360.api.auth.verify_otp"));
		expect(error.kind).toBe("auth");
		expect(error.message).toBe("The code is incorrect. You have 2 more attempts left.");
	});

	it("never shows server internals for a 500", async () => {
		fetchMock.mockResolvedValueOnce(
			json({ exception: "pymysql.err.OperationalError: (2013, 'Lost connection')" }, 500),
		);
		const error = await failure(call("hostel360.api.portal.get_dashboard"));
		expect(error.kind).toBe("server");
		expect(error.message).not.toContain("pymysql");
	});

	it("reports a network failure as offline", async () => {
		const onNetworkStatus = vi.fn();
		configureApi({ onNetworkStatus });
		fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

		const error = await failure(call("hostel360.api.portal.get_dashboard"));

		expect(error.kind).toBe("offline");
		expect(error.isNetwork).toBe(true);
		expect(error.message).toContain("couldn't reach the hostel server");
		expect(onNetworkStatus).toHaveBeenCalledWith(false);
	});

	it("reports the connection as back once a request succeeds", async () => {
		const onNetworkStatus = vi.fn();
		configureApi({ onNetworkStatus });
		fetchMock.mockResolvedValueOnce(json({ message: [] }));
		await call("hostel360.api.portal.list_notifications");
		expect(onNetworkStatus).toHaveBeenCalledWith(true);
	});
});

describe("file downloads", () => {
	it("returns the blob and the server's file name", async () => {
		fetchMock.mockResolvedValueOnce(
			new Response("%PDF-1.7", {
				status: 200,
				headers: {
					"Content-Type": "application/pdf",
					"Content-Disposition": 'attachment; filename="RCPT-2026-00042.pdf"',
				},
			}),
		);
		const file = await callFile("hostel360.api.portal.download_receipt", { payment: "RCPT-2026-00042" }, "receipt.pdf");
		expect(file.filename).toBe("RCPT-2026-00042.pdf");
		expect(await file.blob.text()).toBe("%PDF-1.7");
	});
});

describe("parseServerMessages", () => {
	it("tolerates malformed input", () => {
		expect(parseServerMessages("not json")).toEqual([]);
		expect(parseServerMessages(undefined)).toEqual([]);
		expect(parseServerMessages(JSON.stringify(["plain text"]))).toEqual(["plain text"]);
	});
});

describe("server language", () => {
	it("asks for Tamil server messages while the portal is in Tamil, also for uploads", async () => {
		const { locale } = await import("@/i18n");
		locale.value = "ta";
		fetchMock.mockImplementation(() => Promise.resolve(json({ message: { ok: true } })));

		await call("hostel360.api.portal.cancel_leave", { name: "LV-1" });
		await call("hostel360.api.portal.list_leaves", {}, { method: "GET" });
		const form = new FormData();
		form.append("purpose", "grievance");
		await call("hostel360.api.portal.upload_attachment", form);

		expect(JSON.parse(String(lastRequest(0).init.body))._lang).toBe("ta");
		expect(lastRequest(1).url).toContain("_lang=ta");
		expect((lastRequest(2).init.body as FormData).get("_lang")).toBe("ta");
	});
});

describe("session refusals", () => {
	it("ends the session on a 401 SessionExpired, but not on a wrong sign-in code", async () => {
		const onSessionEnded = vi.fn();
		configureApi({ onSessionEnded });
		fetchMock
			.mockResolvedValueOnce(json({ session_expired: 1, exc_type: "SessionExpired" }, 401))
			.mockResolvedValueOnce(
				json({ exc_type: "AuthenticationError", _server_messages: serverMessages("The code is incorrect.") }, 401),
			);

		const expired = await failure(call("hostel360.api.portal.apply_leave"));
		expect(expired.message).toBe("Your session has ended. Please sign in again.");
		expect(onSessionEnded).toHaveBeenCalledTimes(1);

		await failure(call("hostel360.api.auth.verify_otp"));
		expect(onSessionEnded).toHaveBeenCalledTimes(1);
	});

	it("never shows Frappe's 'not whitelisted' text once the session turns out to have ended", async () => {
		configureApi({ onForbidden: async () => true });
		fetchMock.mockResolvedValueOnce(
			json(
				{
					exc_type: "PermissionError",
					_server_messages: serverMessages(
						"You are not permitted to access this resource. Login to accessFunction <strong>hostel360.api.portal.apply_leave</strong> is not whitelisted.",
					),
				},
				403,
			),
		);
		const error = await failure(call("hostel360.api.portal.apply_leave"));
		expect(error.message).toBe("Your session has ended. Please sign in again.");
		expect(error.message).not.toContain("whitelisted");
	});

	it("does not re-check the session for refusals about one feature or ward", async () => {
		const onForbidden = vi.fn();
		configureApi({ onForbidden });
		fetchMock.mockResolvedValueOnce(
			json(
				{
					exc_type: "PortalValidationPending",
					_server_messages: serverMessages("Bala's registration is waiting for validation."),
				},
				403,
			),
		);
		const error = await failure(call("hostel360.api.portal.get_dashboard"));
		expect(error.message).toBe("Bala's registration is waiting for validation.");
		expect(onForbidden).not.toHaveBeenCalled();
	});

	it("sends the user to a privacy notice published while they were signed in", async () => {
		const onConsentRequired = vi.fn();
		const onForbidden = vi.fn();
		configureApi({ onConsentRequired, onForbidden });
		fetchMock.mockResolvedValueOnce(
			json(
				{
					exc_type: "PortalConsentRequired",
					_server_messages: serverMessages("Please read and accept the privacy notice to continue."),
				},
				403,
			),
		);
		const error = await failure(call("hostel360.api.portal.get_fees"));
		expect(onConsentRequired).toHaveBeenCalledWith(error);
		expect(onForbidden).not.toHaveBeenCalled();
	});

	it("hands a closed account's reason to the app", async () => {
		const onAccessClosed = vi.fn();
		const onForbidden = vi.fn();
		configureApi({ onAccessClosed, onForbidden });
		fetchMock.mockResolvedValueOnce(
			json(
				{
					exc_type: "PortalAccessClosed",
					_server_messages: serverMessages("Portal access for this hostel record has been closed."),
				},
				403,
			),
		);
		const error = await failure(call("hostel360.api.portal.get_session"));
		expect(onAccessClosed).toHaveBeenCalledWith(error);
		expect(onForbidden).not.toHaveBeenCalled();
		expect(error.message).toBe("Portal access for this hostel record has been closed.");
	});
});
